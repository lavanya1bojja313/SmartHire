import os
import re

REPLACEMENTS = [
    (r"from app\.agent\.prompts\.schemas import", "from schemas import"),
    (r"from app\.agent\.prompts\.system_prompt import", "from system_prompt import"),
    (r"from app\.agent\.state_machine import", "from state_machine import"),
    (r"from app\.agent\.tools\.calendar_tools import", "from calendar_tools import"),
    (r"from app\.core\.config import", "from config import"),
    (r"from app\.core\.logging import", "from logging_config import"),
    (r"from app\.models\.models import", "from models import"),
    (r"from app\.core\.database import", "from database import"),
    (r"from app\.services\.calendar import", "from calendar_service import"),
    (r"from app\.services\.google_calendar import", "from google_calendar import"),
    (r"from app\.services\.microsoft_calendar import", "from microsoft_calendar import"),
    (r"from app\.core\.encryption import", "from encryption import"),
    (r"from app\.models\.user import CalendarToken", "from models import InterviewerToken"),
    (r"from app\.models\.user import User", "from models import User"),
    (r"from app\.agent\.orchestrator import", "from orchestrator import"),
    (r"from app\.agent\.state_machine import", "from state_machine import"),
    (r"import calendar\n", "import calendar_service as calendar\n"),
]

def process_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = content
    for pattern, repl in REPLACEMENTS:
        new_content = re.sub(pattern, repl, new_content)
    
    if new_content != content:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Updated {path}")

for file in os.listdir('.'):
    if file.endswith('.py') and file != 'fix_imports.py':
        process_file(file)
