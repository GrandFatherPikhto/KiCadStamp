# kicadstamp/placement/executor/operation_logger.py
import json
import logging
from datetime import datetime
from pathlib import Path


from ...constants import DEFAULT_LOG_DIR
from ...i18n import _

logger = logging.getLogger(__name__)

class OperationLogger:
    def __init__(self, log_dir: str = DEFAULT_LOG_DIR):
        self.log_dir = Path(log_dir)
        # parents=True: the log dir may be nested (e.g. profiles/power/logs/operational)
        # and its intermediate directories may not exist yet.
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def write_operation_log(self, move_log: list[dict], via_log: list[dict],
                            track_log: list[dict] | None = None) -> Path | None:
        track_log = track_log or []
        if not move_log and not via_log and not track_log:
            return None
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_data = {
                'timestamp': datetime.now().isoformat(),
                'moves': move_log,
                'created_vias': via_log,
                'created_tracks': track_log
            }
            filename = self.log_dir / f"operation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, indent=2, ensure_ascii=False)
            logger.info(_("Operation log saved to {file}").format(file=filename))
            return filename
        except Exception as e:
            logger.error(_("Failed to save operation log: {e}").format(e=e))
            return None