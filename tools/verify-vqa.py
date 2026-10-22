"""VQA entry point reusing the production application's maintained launcher."""
from pathlib import Path
import runpy
import sys

sys.argv.append("--vqa")
runpy.run_path(str(Path(__file__).with_name("verify-trialdesk.py")), run_name="__main__")
