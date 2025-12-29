import sys
from jinja2 import Environment, FileSystemLoader

try:
    env = Environment(loader=FileSystemLoader('templates'))
    template = env.get_template('graph.html')
    print("Template loaded successfully")
except Exception as e:
    print(f"Error loading template: {e}")
    sys.exit(1)
