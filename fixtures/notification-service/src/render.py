from pathlib import Path

TEMPLATES = Path(__file__).parent.parent / "templates"


def render_template(template: str, **context) -> str:
    html = (TEMPLATES / template).read_text()
    for key, value in context.items():
        html = html.replace("{{ " + key + " }}", str(value))
    return html
