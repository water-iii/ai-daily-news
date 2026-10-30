"""Main pipeline: collect → preprocess → generate digest → publish."""

import sys
import os
import json
import yaml
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.collectors.rss import collect_rss
from src.processor.preprocessor import preprocess
from src.processor.generator import generate_digest
from src.processor.content_filter import check_content, filter_content
from src.publisher import active_publishers


def _collect_twitter_if_configured(hours):
    if not (os.getenv("X_AUTH_TOKEN") and os.getenv("X_CT0")):
        print("  Skipping Twitter (X_AUTH_TOKEN / X_CT0 not set)")
        return []
    from src.collectors.twitter import collect_twitter
    return collect_twitter(hours=hours)


def _generate_audio_if_configured(digest, date_str):
    if os.getenv("DISABLE_AUDIO"):
        return None
    try:
        from src.audio.podcast import generate_podcast_audio
        return generate_podcast_audio(digest, date_str)
    except Exception as e:
        print(f"  Audio generation failed: {e}")
        return None


def run(hours=24):
    date_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    print(f"=== AI Daily News: {date_str} ===\n")

    print("[1/5] Collecting RSS feeds...")
    rss_items = collect_rss(hours=hours)
    print(f"  Total RSS items: {len(rss_items)}\n")

    print("[2/5] Collecting X/Twitter...")
    twitter_items = _collect_twitter_if_configured(hours)
    print(f"  Total Twitter items: {len(twitter_items)}\n")

    all_items = rss_items + twitter_items
    print(f"Total items collected: {len(all_items)}\n")

    if not all_items:
        raise RuntimeError("No RSS or X items were collected; refusing to report success.")

    os.makedirs("data", exist_ok=True)
    raw_path = f"data/raw-{date_str}.json"
    with open(raw_path, "w") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)
    print(f"Raw data saved to {raw_path}")

    print("\n[3/5] Preprocessing (score → dedup → classify → sort)...")
    with open("config/x_accounts.yaml") as f:
        accounts_config = yaml.safe_load(f)

    processed_items, stats = preprocess(all_items, accounts_config)
    print(
        f"  Original: {stats['original']} → After dedup: {stats['after_dedup']} "
        f"(removed {stats['removed']}) → After filter: {stats['after_filter']} "
        f"(filtered {stats['filtered']})"
    )

    processed_path = f"data/processed-{date_str}.json"
    with open(processed_path, "w") as f:
        json.dump(processed_items, f, ensure_ascii=False, indent=2)
    print(f"  Processed data saved to {processed_path}")

    print(f"\n[4/5] Generating digest with LLM...")
    digest = generate_digest(processed_items, date_str)

    filtered_info = None
    if os.getenv("ENABLE_CONTENT_FILTER"):
        is_safe, flagged = check_content(digest)
        if not is_safe:
            print(f"\n  ⚠ Sensitive content detected: {flagged}")
            digest = filter_content(digest)
            filtered_info = ", ".join(flagged)

    print(f"\n[4.5/5] Generating podcast audio...")
    audio_path = _generate_audio_if_configured(digest, date_str)
    if audio_path:
        print(f"  Audio saved to {audio_path}")

    publishers_env = os.getenv("PUBLISHERS")
    names = [n.strip() for n in publishers_env.split(",")] if publishers_env else None
    publishers = active_publishers(names)
    if not publishers:
        raise RuntimeError("No publisher is configured. Check PUBLISHERS and publisher credentials.")

    print(f"\n[5/5] Publishing via: {[p.NAME for p in publishers]}")
    publish_errors = []
    for pub in publishers:
        try:
            result = pub.publish(
                digest, date_str, audio_path=audio_path, filtered_info=filtered_info
            )
            print(f"  [{pub.NAME}] {result}")
        except Exception as e:
            publish_errors.append(f"{pub.NAME}: {type(e).__name__}: {e}")
            print(f"  [{pub.NAME}] failed: {type(e).__name__}: {e}")

    if publish_errors:
        raise RuntimeError("One or more publishers failed: " + "; ".join(publish_errors))

    print("\n" + "=" * 50)
    print(digest)


if __name__ == "__main__":
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    run(hours=hours)
