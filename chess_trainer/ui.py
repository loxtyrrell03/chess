from __future__ import annotations

from pathlib import Path

import chess
from PySide6.QtCore import QSize, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QColor, QDesktopServices, QFont, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .config import AppConfig, install_root
from .events import AppEvent
from .runtime import RuntimeController


STYLE = """
QWidget { background:#0f1319; color:#e8edf3; font-family:"Segoe UI"; font-size:10pt; }
QMainWindow { background:#0f1319; }
QFrame#card { background:#171d26; border:1px solid #283241; border-radius:12px; }
QLabel#title { font-size:21pt; font-weight:750; color:#f8fafc; }
QLabel#subtitle { color:#8f9bad; }
QLabel#section { color:#73d7b5; font-size:8.5pt; font-weight:750; letter-spacing:1px; }
QLabel#value { font-size:11.5pt; font-weight:650; color:#f4f7fa; }
QPushButton { background:#252f3d; border:1px solid #39475a; border-radius:8px; padding:8px 13px; }
QPushButton:hover { background:#303d4f; }
QPushButton:checked { background:#1d7259; border-color:#3cac87; }
QPushButton#primary { background:#3ab087; color:#071a13; border:0; font-weight:750; }
QPushButton#primary:hover { background:#52c79d; }
QTextEdit { background:#10151c; border:0; border-radius:7px; color:#cbd5df; padding:8px; }
"""


PIECE_GLYPHS = {
    "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
}


class BoardPreview(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._board: chess.Board | None = None
        self._orientation = "white"
        self.setMinimumSize(290, 290)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(360, 360)

    def set_position(self, fen: str, orientation: str) -> None:
        try:
            self._board = chess.Board(fen) if fen else None
        except ValueError:
            self._board = None
        if orientation.lower() in {"white", "black"}:
            self._orientation = orientation.lower()
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        square = side / 8
        left = (self.width() - side) / 2
        top = (self.height() - side) / 2
        light = QColor("#d8dfca")
        dark = QColor("#78906b")
        painter.setFont(QFont("Segoe UI Symbol", max(16, int(square * 0.62))))

        for row in range(8):
            for column in range(8):
                if self._orientation == "white":
                    file_index, rank_index = column, 7 - row
                else:
                    file_index, rank_index = 7 - column, row
                painter.fillRect(int(left + column * square), int(top + row * square), int(square + 1), int(square + 1), light if (file_index + rank_index) % 2 else dark)
                if not self._board:
                    continue
                piece = self._board.piece_at(chess.square(file_index, rank_index))
                if not piece:
                    continue
                glyph = PIECE_GLYPHS[piece.symbol()]
                painter.setPen(QColor("#f7f4eb") if piece.color else QColor("#18202a"))
                painter.drawText(
                    int(left + column * square),
                    int(top + row * square),
                    int(square),
                    int(square),
                    Qt.AlignCenter,
                    glyph,
                )


class ControlCentre(QMainWindow):
    def __init__(self, config: AppConfig, runtime: RuntimeController, icon: QIcon) -> None:
        super().__init__()
        self.config = config
        self.runtime = runtime
        self.icon = icon
        self._quitting = False
        self._orientation = "white"
        self.setWindowTitle("Chess Trainer")
        self.setWindowIcon(icon)
        self.resize(920, 720)
        self.setMinimumSize(780, 620)
        self.setStyleSheet(STYLE)
        self._build_ui()
        self._build_tray()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_events)
        self.poll_timer.start(100)

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        headings = QVBoxLayout()
        title = QLabel("Chess Trainer")
        title.setObjectName("title")
        subtitle = QLabel("Continuous Stockfish analysis · automatic local connection")
        subtitle.setObjectName("subtitle")
        headings.addWidget(title)
        headings.addWidget(subtitle)
        header.addLayout(headings)
        header.addStretch()
        self.connection_badge = QLabel("Starting…")
        self.connection_badge.setStyleSheet("color:#f2c66d;font-weight:700")
        header.addWidget(self.connection_badge)
        root.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(12)

        board_card = QFrame()
        board_card.setObjectName("card")
        board_layout = QVBoxLayout(board_card)
        board_layout.setContentsMargins(14, 12, 14, 14)
        board_title = QLabel("CURRENT BOARD")
        board_title.setObjectName("section")
        board_layout.addWidget(board_title)
        self.board_preview = BoardPreview()
        board_layout.addWidget(self.board_preview, stretch=1)
        content.addWidget(board_card, stretch=5)

        side = QVBoxLayout()
        side.setSpacing(12)

        status_card = QFrame()
        status_card.setObjectName("card")
        status_grid = QGridLayout(status_card)
        status_grid.setContentsMargins(14, 12, 14, 12)
        status_grid.setHorizontalSpacing(18)
        status_grid.setVerticalSpacing(10)
        self.status_values: dict[str, QLabel] = {}
        for index, (caption, key, initial) in enumerate([
            ("SYNC", "sync", "Waiting"),
            ("SIDE", "side", "Unknown"),
            ("ORIENTATION", "orientation", "Unknown"),
            ("LAST MOVE", "last_move", "—"),
        ]):
            row, column = divmod(index, 2)
            block = QVBoxLayout()
            label = QLabel(caption)
            label.setObjectName("section")
            value = QLabel(initial)
            value.setObjectName("value")
            self.status_values[key] = value
            block.addWidget(label)
            block.addWidget(value)
            status_grid.addLayout(block, row, column)
        side.addWidget(status_card)

        moves_card = QFrame()
        moves_card.setObjectName("card")
        moves_layout = QVBoxLayout(moves_card)
        moves_layout.setContentsMargins(14, 12, 14, 14)
        moves_title = QLabel("MOVE LIST")
        moves_title.setObjectName("section")
        moves_layout.addWidget(moves_title)
        self.moves = QTextEdit()
        self.moves.setReadOnly(True)
        self.moves.setPlaceholderText("Waiting for move history…")
        moves_layout.addWidget(self.moves)
        side.addWidget(moves_card, stretch=1)
        content.addLayout(side, stretch=4)
        root.addLayout(content, stretch=1)

        analysis_card = QFrame()
        analysis_card.setObjectName("card")
        analysis_layout = QHBoxLayout(analysis_card)
        analysis_layout.setContentsMargins(16, 12, 16, 12)
        analysis_text = QVBoxLayout()
        section = QLabel("STOCKFISH")
        section.setObjectName("section")
        analysis_text.addWidget(section)
        self.best_move_label = QLabel("Waiting for a synchronized position")
        self.best_move_label.setStyleSheet("font-size:15pt;font-weight:750")
        analysis_text.addWidget(self.best_move_label)
        self.pv_label = QLabel("Principal variation will appear here")
        self.pv_label.setObjectName("subtitle")
        self.pv_label.setWordWrap(True)
        analysis_text.addWidget(self.pv_label)
        analysis_layout.addLayout(analysis_text, stretch=1)
        self.eval_label = QLabel("—")
        self.eval_label.setStyleSheet("font-size:21pt;font-weight:800;color:#73d7b5")
        analysis_layout.addWidget(self.eval_label)
        root.addWidget(analysis_card)

        controls = QHBoxLayout()
        self.monitor_button = QPushButton()
        self.monitor_button.setCheckable(True)
        self.monitor_button.setChecked(self.config.monitoring)
        self.monitor_button.toggled.connect(self._set_monitoring)
        self._set_monitor_button_text(self.config.monitoring)
        controls.addWidget(self.monitor_button)

        recalibrate = QPushButton("Recalibrate")
        recalibrate.clicked.connect(self.runtime.recalibrate)
        controls.addWidget(recalibrate)

        analyze = QPushButton("Analyze now")
        analyze.setObjectName("primary")
        analyze.clicked.connect(self.runtime.analyze_now)
        controls.addWidget(analyze)

        self.overlay_button = QPushButton("Board overlays")
        self.overlay_button.setCheckable(True)
        self.overlay_button.setChecked(self.config.show_arrow)
        self.overlay_button.toggled.connect(self.runtime.set_show_arrow)
        controls.addWidget(self.overlay_button)
        controls.addStretch()

        extension_folder = QPushButton("Extension folder")
        extension_folder.clicked.connect(self._open_extension_folder)
        controls.addWidget(extension_folder)
        root.addLayout(controls)

        footer = QHBoxLayout()
        self.activity_label = QLabel("Starting local services…")
        self.activity_label.setObjectName("subtitle")
        footer.addWidget(self.activity_label, stretch=1)
        profile = QLabel(f"{self.config.threads} threads · {self.config.hash_mb // 1024} GB hash")
        profile.setObjectName("subtitle")
        footer.addWidget(profile)
        root.addLayout(footer)
        self.setCentralWidget(central)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip("Chess Trainer")
        menu = QMenu()
        show_action = QAction("Open Chess Trainer", self)
        show_action.triggered.connect(self.show_and_raise)
        menu.addAction(show_action)
        self.tray_monitor_action = QAction("Monitoring", self, checkable=True)
        self.tray_monitor_action.setChecked(self.config.monitoring)
        self.tray_monitor_action.toggled.connect(self.monitor_button.setChecked)
        menu.addAction(self.tray_monitor_action)
        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _poll_events(self) -> None:
        while not self.runtime.events.empty():
            self._apply_event(self.runtime.events.get())

    def _apply_event(self, event: AppEvent) -> None:
        self.activity_label.setText(event.message)
        data = event.data
        if event.kind == "bridge":
            connected = bool(data.get("connected"))
            self.connection_badge.setText("Connected" if connected else "Waiting for Chrome")
            self.connection_badge.setStyleSheet(
                "color:#73d7b5;font-weight:700" if connected else "color:#f2c66d;font-weight:700"
            )
        elif event.kind == "position":
            self._orientation = str(data.get("orientation", "white")).lower()
            self.board_preview.set_position(str(data.get("fen") or ""), self._orientation)
            self.moves.setPlainText(_format_moves(tuple(data.get("moves") or ())))
            self.moves.verticalScrollBar().setValue(self.moves.verticalScrollBar().maximum())
            self.status_values["sync"].setText(str(data.get("sync", "waiting")).title())
            self.status_values["side"].setText(str(data.get("player_color", "Unknown")))
            self.status_values["orientation"].setText(self._orientation.title())
            self.status_values["last_move"].setText(str(data.get("last_move") or "—"))
        elif event.kind == "analysis":
            if data.get("thinking"):
                self.best_move_label.setText("Stockfish is analyzing…")
            elif data.get("best_move"):
                self.best_move_label.setText(f"{data.get('best_move_san', '')}  ·  {data.get('best_move', '')}")
                self.eval_label.setText(str(data.get("evaluation", "—")))
                depth = data.get("depth")
                suffix = f" · depth {depth}" if depth else ""
                self.pv_label.setText(f"{data.get('pv') or '—'}{suffix}")
        elif event.kind == "control" and "monitoring" in data:
            enabled = bool(data["monitoring"])
            self.tray_monitor_action.blockSignals(True)
            self.tray_monitor_action.setChecked(enabled)
            self.tray_monitor_action.blockSignals(False)
        elif event.kind == "error":
            self.connection_badge.setText("Needs attention")
            self.connection_badge.setStyleSheet("color:#ff7b86;font-weight:700")

    def _set_monitoring(self, enabled: bool) -> None:
        self._set_monitor_button_text(enabled)
        self.runtime.set_monitoring(enabled)

    def _set_monitor_button_text(self, enabled: bool) -> None:
        self.monitor_button.setText("Pause monitoring" if enabled else "Resume monitoring")

    def _open_extension_folder(self) -> None:
        path = install_root() / "extension"
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QMessageBox.warning(self, "Extension not found", f"Extension folder not found:\n{path}")

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick}:
            self.show_and_raise()

    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.config.minimize_to_tray and not self._quitting:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "Chess Trainer is still running",
                "Click the tray knight to reopen it.",
                QSystemTrayIcon.Information,
                2200,
            )
            return
        event.accept()

    def quit_app(self) -> None:
        self._quitting = True
        self.poll_timer.stop()
        self.runtime.stop()
        self.tray.hide()
        QApplication.quit()


def _format_moves(moves: tuple[str, ...]) -> str:
    if not moves:
        return ""
    rows: list[str] = []
    for index in range(0, len(moves), 2):
        move_number = index // 2 + 1
        white = moves[index]
        black = moves[index + 1] if index + 1 < len(moves) else ""
        rows.append(f"{move_number:>2}.  {white:<9} {black}")
    return "\n".join(rows)
