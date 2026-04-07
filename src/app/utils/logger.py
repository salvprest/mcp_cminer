import logging
from rich.logging import RichHandler


class MaxLevelFilter(logging.Filter):
    """
    A filter that only allows logs BELOW a certain level.
    Used to prevent the 'normal' handler from printing errors (which are handled by the error handler).
    """
    def __init__(self, level):
        super().__init__()
        self.level = level

    def filter(self, record):
        return record.levelno <= self.level

def setup_logging(level: str = "INFO", name: str = "my_app") -> logging.Logger:
    """
    Configures the root logger to be quiet by default, while allowing
    the specific app logger to be verbose (DEBUG) if requested.
    """

    # Define the Handlers
    # Normal Handler: Handles everything UP TO Warning (Debug, Info, Warning)
    # We use show_path=False to keep standard logs clean
    normal_handler = RichHandler(
        rich_tracebacks=True,
        show_time=False,
        omit_repeated_times=False,
        log_time_format="[%X]",
        show_path=True,
        markup=True
    )
    # Filter: Only allow logs <= WARNING. 
    # (Errors go to the error_handler, not this one)
    normal_handler.addFilter(MaxLevelFilter(logging.WARNING))

    # Error Handler: Handles ERROR and CRITICAL
    # We use show_path=True to trace where the crash happened
    error_handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        omit_repeated_times=False,
        log_time_format="[%X]",
        show_path=True,
        markup=True
    )
    error_handler.setLevel(logging.ERROR)

    # Configure the ROOT Logger
    # Always set Root to INFO (or WARNING). 
    # Never set Root to DEBUG, or every library will scream at you.
    logging.basicConfig(
        level=logging.INFO, 
        format="%(message)s",
        handlers=[normal_handler, error_handler],
        force=True # Ensures we override any existing config
    )

    # Silence Third-Party Noise
    # Even at INFO level, some libs are noisy. Silence them here.
    noisy_modules = [
        "uvicorn", "uvicorn.access", "fastapi", "httpx", 
        "autogen_core", "openai", "httpcore", "urllib3", 
        "asyncio", "multipart"
    ]
    for module in noisy_modules:
        logging.getLogger(module).setLevel(logging.WARNING)

    # Configure YOUR App's Logger
    # This is where we enable DEBUG only for this specific name
    app_logger = logging.getLogger(name)
    app_logger.setLevel(level.upper())

    return app_logger