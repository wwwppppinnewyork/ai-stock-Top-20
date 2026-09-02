from pathlib import Path
import shutil


WORKDIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = WORKDIR / "output"
SITE_DIR = WORKDIR / "site"


def copy_file(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def main():
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    copy_file(OUTPUT_DIR / "dashboard.html", SITE_DIR / "index.html")
    copy_file(OUTPUT_DIR / "candidate_pool.csv", SITE_DIR / "candidate_pool.csv")
    copy_file(OUTPUT_DIR / "ranked_stocks.csv", SITE_DIR / "ranked_stocks.csv")
    copy_file(OUTPUT_DIR / "top_picks.csv", SITE_DIR / "top_picks.csv")
    print(SITE_DIR)


if __name__ == "__main__":
    main()
