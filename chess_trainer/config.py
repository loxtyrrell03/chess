from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


APP_NAME = "ChessTrainer"


def app_data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


@dataclass(slots=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    engine_path: str = ""
    threads: int = 14
    hash_mb: int = 4096
    think_time_ms: int = 2000
    multi_pv: int = 1
    skill_level: int = 20
    monitoring: bool = True
    auto_play: bool = True
    show_arrow: bool = True
    minimize_to_tray: bool = True
    syzygy_path: str = ""
    engine_kind: str = "stockfish"
    odds_mode: str = "none"
    lc0_path: str = ""
    lc0_sc_path: str = ""
    lc0_bt4_weights: str = ""
    lc0_t1_odds_weights: str = ""
    lc0_queen_odds_weights: str = ""
    lc0_contempt: int = 0
    lc0_auto_network: bool = False
    lc0_auto_contempt: bool = False
    analyze_opponent: bool = False
    show_opponent_arrows: bool = False

    @classmethod
    def load(cls) -> AppConfig:
        path = app_data_dir() / "config.json"
        values: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                values = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError):
                values = {}

        allowed = {item.name for item in fields(cls)}
        config = cls(**{key: value for key, value in values.items() if key in allowed})
        if getattr(sys, "frozen", False) or not config.engine_path or not Path(config.engine_path).is_file():
            config.engine_path = str(default_engine_path())
        lc0 = default_lc0_paths()
        if not config.lc0_path:
            config.lc0_path = str(lc0["engine"])
        if not config.lc0_sc_path:
            config.lc0_sc_path = str(lc0["queen_engine"])
        if not config.lc0_bt4_weights:
            config.lc0_bt4_weights = str(lc0["strongest"])
        if not config.lc0_t1_odds_weights:
            config.lc0_t1_odds_weights = str(lc0["odds"])
        if not config.lc0_queen_odds_weights:
            config.lc0_queen_odds_weights = str(lc0["queen_odds"])
        config.validate()
        config.save()
        return config

    def validate(self) -> None:
        self.host = "127.0.0.1"
        self.port = min(65535, max(1024, _int_or(self.port, 8765)))
        self.threads = min(16, max(1, _int_or(self.threads, 14)))
        self.hash_mb = min(16384, max(16, _int_or(self.hash_mb, 4096)))
        self.think_time_ms = min(120_000, max(100, _int_or(self.think_time_ms, 2000)))
        self.multi_pv = min(3, max(1, _int_or(self.multi_pv, 1)))
        self.skill_level = 20
        self.engine_path = str(self.engine_path or default_engine_path())
        self.syzygy_path = str(self.syzygy_path or "")
        self.engine_kind = str(self.engine_kind or "stockfish").lower()
        if self.engine_kind not in {"stockfish", "lc0"}:
            self.engine_kind = "stockfish"
        self.odds_mode = str(self.odds_mode or "none").lower()
        if self.odds_mode not in {"none", "knight", "rook", "queen_for_knight", "queen"}:
            self.odds_mode = "none"
        self.lc0_path = str(self.lc0_path or default_lc0_paths()["engine"])
        self.lc0_sc_path = str(self.lc0_sc_path or default_lc0_paths()["queen_engine"])
        self.lc0_bt4_weights = str(self.lc0_bt4_weights or default_lc0_paths()["strongest"])
        self.lc0_t1_odds_weights = str(self.lc0_t1_odds_weights or default_lc0_paths()["odds"])
        self.lc0_queen_odds_weights = str(self.lc0_queen_odds_weights or default_lc0_paths()["queen_odds"])
        self.lc0_contempt = min(1000, max(-1000, _int_or(self.lc0_contempt, 0)))
        self.lc0_auto_network = _bool_or(self.lc0_auto_network, False)
        self.lc0_auto_contempt = _bool_or(self.lc0_auto_contempt, False)
        self.monitoring = _bool_or(self.monitoring, True)
        self.auto_play = _bool_or(self.auto_play, True)
        self.show_arrow = _bool_or(self.show_arrow, True)
        self.minimize_to_tray = _bool_or(self.minimize_to_tray, True)
        self.analyze_opponent = _bool_or(self.analyze_opponent, False)
        self.show_opponent_arrows = _bool_or(self.show_opponent_arrows, False)

    def save(self) -> None:
        self.validate()
        path = app_data_dir() / "config.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        temporary.replace(path)


def default_engine_path() -> Path:
    candidates = [
        install_root() / "vendor" / "stockfish-18" / "stockfish-windows-x86-64-bmi2.exe",
        install_root() / "engine" / "stockfish-windows-x86-64-bmi2.exe",
        app_data_dir() / "engine" / "stockfish-windows-x86-64-bmi2.exe",
    ]
    return next((path for path in candidates if path.exists()), candidates[0])


def default_lc0_paths() -> dict[str, Path]:
    root = app_data_dir() / "engines" / "lc0-v0.32.1-fresh"
    queen_root = root / "queen-odds"
    return {
        "engine": root / "lc0.exe",
        "queen_engine": root / "lc0.exe",
        "strongest": root / "BT4-it332.pb.gz",
        "odds": root / "T1-odds.pb.gz",
        "queen_odds": queen_root / "lqo_v2.pb.gz",
    }


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _bool_or(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in {"true", "1", "yes", "on"}:
            return True
        if value.lower() in {"false", "0", "no", "off"}:
            return False
    return default
