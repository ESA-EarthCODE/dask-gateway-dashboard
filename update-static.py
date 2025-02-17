# TODO: run this as part of pip install
from pathlib import Path
from urllib.request import urlretrieve

pure_css_version = "3.0.0"

base_url = f"https://cdn.jsdelivr.net/npm/purecss@{pure_css_version}"

static = Path(__file__).parent.resolve() / "static"
pure_css = static / "pure-css"

pure_css.mkdir(parents=True, exist_ok=True)

for fname in ("pure-min.css", "grids-responsive-min.css"):
    url = base_url + f"/build/{fname}"
    dest = pure_css / fname
    print(f"Downloading {url} -> {dest}")
    urlretrieve(url, dest)
