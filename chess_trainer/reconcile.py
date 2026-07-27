from __future__ import annotations

from dataclasses import dataclass

import chess

from .models import Snapshot, SyncState, Transition


STARTING_PLACEMENT = chess.Board().board_fen()


def piece_map(board: chess.Board) -> dict[str, str]:
    return {chess.square_name(square): piece.symbol() for square, piece in board.piece_map().items()}


def placement_matches(board: chess.Board, observed: dict[str, str]) -> bool:
    return piece_map(board) == observed


@dataclass(slots=True)
class GameReconciler:
    board: chess.Board | None = None
    game_key: str | None = None
    revision: int = 0
    last_seq: int = -1

    def reset(self, snapshot: Snapshot) -> None:
        self.game_key = snapshot.game_key
        self.last_seq = -1
        starting_fen = snapshot.starting_fen or chess.STARTING_FEN
        try:
            self.board = chess.Board(starting_fen)
        except ValueError:
            self.board = chess.Board()
        self.revision += 1

    def _declared_position(self, snapshot: Snapshot) -> chess.Board | None:
        if not snapshot.current_fen:
            return None
        try:
            board = chess.Board(snapshot.current_fen)
        except ValueError:
            return None
        if not board.is_valid():
            return None
        if snapshot.pieces and not placement_matches(board, snapshot.pieces):
            return None
        return board

    def ingest(self, snapshot: Snapshot) -> Transition:
        changed_game = self.board is None or self.game_key != snapshot.game_key
        if not changed_game and snapshot.seq <= self.last_seq:
            return Transition(
                SyncState.TRANSIENT,
                self.revision,
                self.board.copy(stack=True) if self.board else None,
                message="Duplicate or out-of-order snapshot",
            )

        new_game = changed_game
        if not new_game and self.board and self.board.board_fen() != STARTING_PLACEMENT:
            new_game = _is_fresh_start(snapshot)

        if new_game:
            self.reset(snapshot)

        self.last_seq = snapshot.seq
        assert self.board is not None

        replayed = self._replay_history(snapshot)
        if replayed is not None:
            changed = replayed.fen() != self.board.fen()
            inferred = tuple(replayed.move_stack[len(self.board.move_stack):]) if _same_root(self.board, replayed) else ()
            self.board = replayed
            if changed:
                self.revision += 1
            return Transition(
                SyncState.SYNCHRONIZED,
                self.revision,
                self.board.copy(stack=True),
                new_game=new_game,
                moves=inferred,
                message="Synchronized from move history",
            )

        declared = self._declared_position(snapshot)
        if declared is not None:
            changed = declared.fen() != self.board.fen()
            self.board = declared
            if changed:
                self.revision += 1
            return Transition(
                SyncState.SYNCHRONIZED,
                self.revision,
                self.board.copy(stack=True),
                new_game=new_game,
                message="Synchronized from current FEN",
            )

        if not snapshot.pieces:
            if new_game and snapshot.starting_fen and not snapshot.moves:
                return Transition(
                    SyncState.SYNCHRONIZED,
                    self.revision,
                    self.board.copy(stack=True),
                    new_game=True,
                    message="Synchronized from starting FEN",
                )
            return Transition(
                SyncState.WAITING,
                self.revision,
                self.board.copy(stack=True),
                new_game=new_game,
                message="Waiting for complete move history from the board renderer",
            )

        if placement_matches(self.board, snapshot.pieces):
            return Transition(
                SyncState.SYNCHRONIZED,
                self.revision,
                self.board.copy(stack=True),
                new_game=new_game,
                message="Position unchanged",
            )

        one_ply = _matching_moves(self.board, snapshot.pieces)
        if len(one_ply) == 1:
            move = one_ply[0]
            self.board.push(move)
            self.revision += 1
            return Transition(
                SyncState.SYNCHRONIZED,
                self.revision,
                self.board.copy(stack=True),
                new_game=new_game,
                moves=(move,),
                message=f"Inferred {move.uci()} from legal position change",
            )

        two_ply = _matching_two_ply(self.board, snapshot.pieces)
        if len(two_ply) == 1:
            moves = two_ply[0]
            for move in moves:
                self.board.push(move)
            self.revision += len(moves)
            return Transition(
                SyncState.SYNCHRONIZED,
                self.revision,
                self.board.copy(stack=True),
                new_game=new_game,
                moves=moves,
                message="Recovered two missed plies",
            )

        if new_game:
            try:
                starting_board = chess.Board(snapshot.starting_fen or chess.STARTING_FEN)
            except ValueError:
                starting_board = None
            if starting_board is not None and snapshot.pieces == piece_map(starting_board):
                return Transition(SyncState.SYNCHRONIZED, self.revision, self.board.copy(stack=True), new_game=True)

        # A content-script reload can attach halfway through a game. If one
        # historical SAN token cannot be replayed, there is no previous board
        # from which to infer the latest move. On that first attachment only,
        # retain the observed placement as a provisional recovery. Runtime must
        # not analyze it or change networks until later history, a declared FEN,
        # or a legal transition confirms it.
        #
        # Once a game has an established board, never replace it with an
        # arbitrary placement. Chess.com and Chessground animations can expose
        # a captured piece disappearing before the mover reaches its target.
        # The following complete frame will match a legal move from the retained
        # board and can then be accepted immediately.
        observed = _observed_position(snapshot)
        if observed is not None and new_game:
            changed = observed.fen() != self.board.fen()
            self.board = observed
            if changed:
                self.revision += 1
            return Transition(
                SyncState.SYNCHRONIZED,
                self.revision,
                self.board.copy(stack=True),
                new_game=new_game,
                message="Recovered from observed board placement",
                provisional=True,
            )

        reason = "No legal transition matches the observed board"
        if len(one_ply) > 1 or len(two_ply) > 1:
            reason = "Observed board has more than one legal interpretation"
        return Transition(SyncState.TRANSIENT, self.revision, self.board.copy(stack=True), new_game=new_game, message=reason)

    def _replay_history(self, snapshot: Snapshot) -> chess.Board | None:
        if not snapshot.moves:
            return None
        try:
            board = chess.Board(snapshot.starting_fen or chess.STARTING_FEN)
            for token in snapshot.moves:
                cleaned = _clean_move_token(token)
                if not cleaned:
                    continue
                try:
                    move = board.parse_san(cleaned)
                except ValueError:
                    move = chess.Move.from_uci(cleaned.lower())
                    if move not in board.legal_moves:
                        return None
                board.push(move)
            if snapshot.pieces and not placement_matches(board, snapshot.pieces):
                return None
            if snapshot.current_fen:
                try:
                    current = chess.Board(snapshot.current_fen)
                except ValueError:
                    return None
                # Clock counters are not needed for legality; all other FEN
                # fields must agree with the replayed history.
                if board.fen(en_passant="fen").split()[:4] != current.fen(en_passant="fen").split()[:4]:
                    return None
            return board
        except (ValueError, IndexError):
            return None


def _matching_moves(board: chess.Board, observed: dict[str, str]) -> list[chess.Move]:
    matches: list[chess.Move] = []
    for move in list(board.legal_moves):
        board.push(move)
        if placement_matches(board, observed):
            matches.append(move)
        board.pop()
    return matches


def _matching_two_ply(board: chess.Board, observed: dict[str, str]) -> list[tuple[chess.Move, chess.Move]]:
    matches: list[tuple[chess.Move, chess.Move]] = []
    for first in list(board.legal_moves):
        board.push(first)
        for second in list(board.legal_moves):
            board.push(second)
            if placement_matches(board, observed):
                matches.append((first, second))
                if len(matches) > 1:
                    board.pop()
                    board.pop()
                    return matches
            board.pop()
        board.pop()
    return matches


def _clean_move_token(token: str) -> str:
    token = token.strip().replace("…", "...")
    if not token or token.endswith(".") or token in {"1-0", "0-1", "1/2-1/2", "*"}:
        return ""
    if "." in token:
        token = token.rsplit(".", 1)[-1]
    return token.strip()


def _same_root(left: chess.Board, right: chess.Board) -> bool:
    return left.root().fen() == right.root().fen() and len(right.move_stack) >= len(left.move_stack)


def _is_fresh_start(snapshot: Snapshot) -> bool:
    return not snapshot.moves and _pieces_to_board_fen(snapshot.pieces) == STARTING_PLACEMENT


def _pieces_to_board_fen(pieces: dict[str, str]) -> str:
    board = chess.Board.empty()
    for square_name, symbol in pieces.items():
        board.set_piece_at(chess.parse_square(square_name), chess.Piece.from_symbol(symbol))
    return board.board_fen()


def _observed_position(snapshot: Snapshot) -> chess.Board | None:
    if not snapshot.pieces:
        return None
    turn = snapshot.side_to_move
    if turn is None and snapshot.moves:
        try:
            starting_turn = chess.Board(snapshot.starting_fen or chess.STARTING_FEN).turn
        except ValueError:
            starting_turn = chess.WHITE
        turn = starting_turn if len(snapshot.moves) % 2 == 0 else not starting_turn
    if turn is None:
        return None
    fullmove = max(1, len(snapshot.moves) // 2 + 1)
    fen = f"{_pieces_to_board_fen(snapshot.pieces)} {'w' if turn else 'b'} - - 0 {fullmove}"
    try:
        board = chess.Board(fen)
    except ValueError:
        return None
    return board if board.is_valid() and placement_matches(board, snapshot.pieces) else None
