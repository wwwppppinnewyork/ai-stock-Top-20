from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
WEB_PUBLIC = ROOT / "webapp" / "public"


def copy_file(src: Path, name: str):
    WEB_PUBLIC.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, WEB_PUBLIC / name)


def main():
    copy_file(OUTPUT / "dashboard.html", "dashboard.html")
    copy_file(OUTPUT / "candidate_pool.csv", "candidate_pool.csv")
    copy_file(OUTPUT / "ranked_stocks.csv", "ranked_stocks.csv")
    copy_file(OUTPUT / "top_picks.csv", "top_picks.csv")
    print(WEB_PUBLIC)


if __name__ == "__main__":
    main()
