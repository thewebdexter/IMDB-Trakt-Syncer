import os
import sys
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from IMDBTraktSyncer import runtime

# Keep log.txt bounded so a long running (24/7) install does not grow it forever.
# Override with IMDBTRAKTSYNCER_MAX_LOG_BYTES (0 disables the limit).
try:
    MAX_LOG_BYTES = int(os.environ.get("IMDBTRAKTSYNCER_MAX_LOG_BYTES", 5 * 1024 * 1024))
except ValueError:
    MAX_LOG_BYTES = 5 * 1024 * 1024

class CustomFormatter(logging.Formatter):
    def formatException(self, exc_info):
        result = super().formatException(exc_info)
        return f"{result}"

    def format(self, record):
        if record.exc_info:
            exc_info = record.exc_info
            record.exc_info = None
            message = super().format(record)
            record.exc_info = exc_info
            traceback_message = self.formatException(record.exc_info)
            return f"{'`' * 100}\n{message}\n{traceback_message}\n{'`' * 100}\n"
        else:
            message = super().format(record)
            return f"{'`' * 100}\n{message}\n{'`' * 100}\n"

class PrependFileHandler(logging.Handler):
    def __init__(self, filename):
        super().__init__()
        self.filename = filename

    def emit(self, record):
        try:
            log_entry = self.format(record) + "\n"
            if os.path.exists(self.filename):
                with open(self.filename, "r+", encoding="utf-8", errors="replace") as file:
                    old_content = file.read()
                    new_content = log_entry + old_content
                    if MAX_LOG_BYTES > 0 and len(new_content) > MAX_LOG_BYTES:
                        # Newest entries are at the top, so drop the oldest from the bottom
                        new_content = new_content[:MAX_LOG_BYTES]
                    file.seek(0)
                    file.write(new_content)
                    file.truncate()
            else:
                with open(self.filename, "w", encoding="utf-8") as file:
                    file.write(log_entry)
        except Exception:
            print("Error writing log.")

# Package directory by default, or IMDBTRAKTSYNCER_DATA_DIR when set
script_dir = runtime.get_data_directory()
log_file = os.path.join(script_dir, "log.txt")

# Create a custom file handler for prepending logs
handler = PrependFileHandler(log_file)
handler.setLevel(logging.ERROR)

# Set the log format
formatter = CustomFormatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)

# Get the root logger and add the handler
logger = logging.getLogger("")
logger.setLevel(logging.ERROR)
logger.addHandler(handler)