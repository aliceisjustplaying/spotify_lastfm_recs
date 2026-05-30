import re


GENERATED_NAME_RE = re.compile(
    r"\b(discover weekly|release radar|daily mix|radio|wrapped|top songs|spotify\.me|the sound of|the pulse of|needle|"
    r"this is |official|hits|best of|complete collection|soundtrack|original motion picture soundtrack|deluxe|"
    r"anniversary|remaster|now that'?s what i call music|topp 20)\b",
    re.I,
)
ALBUMISH_RE = re.compile(r"\s[–-]\s|original motion picture soundtrack|deluxe|remaster|anniversary|complete edition", re.I)
PERSONAL_OWNER_IDS = {"ktamas"}
SHORTHAND_PERSONAL_RE = re.compile(
    r"\b(shazam|banger|vibes?|gym|dance|work|working|party|christmas|xmas|metal|punk|pop|sad|chill|bath|cleaning|"
    r"manifesting|random|liked|favorites?|favourites?|top songs|songs for|songs to)\b",
    re.I,
)


BUCKET_WEIGHTS = {
    "personal_manual": 1.0,
    "personal_album_collection": 0.55,
    "personal_generated_or_archive": 0.45,
    "collaborative_or_friend": 0.7,
    "external_high_overlap": 0.55,
    "external_album_or_collection": 0.35,
    "external_weak_context": 0.2,
    "external_unknown": 0.35,
}


def classify_playlist(row):
    name = row["name"] or ""
    owner = row["owner_id"] or ""
    followers = int(row["followers_total"] or 0)
    tracks_total = int(row["tracks_total"] or 0)
    collaborative = bool(row["collaborative"])
    listened_ratio = float(row.get("listened_ratio") or 0)
    reasons = []

    if owner in PERSONAL_OWNER_IDS:
        reasons.append("owned_by_you")
    if collaborative:
        reasons.append("collaborative")
    if owner == "spotify":
        reasons.append("spotify_owned")
    if GENERATED_NAME_RE.search(name):
        reasons.append("generated_or_editorial_name")
    if ALBUMISH_RE.search(name):
        reasons.append("album_or_soundtrack_name")
    if SHORTHAND_PERSONAL_RE.search(name):
        reasons.append("personal_intent_name")
    if followers >= 1000 and owner not in PERSONAL_OWNER_IDS:
        reasons.append("high_follower_external")
    if tracks_total <= 15 and ALBUMISH_RE.search(name):
        reasons.append("short_albumish_playlist")
    if listened_ratio >= 0.25 and owner not in PERSONAL_OWNER_IDS:
        reasons.append("high_overlap_with_listens")

    if owner in PERSONAL_OWNER_IDS and "album_or_soundtrack_name" in reasons:
        bucket = "personal_album_collection"
    elif owner in PERSONAL_OWNER_IDS and "generated_or_editorial_name" in reasons:
        bucket = "personal_generated_or_archive"
    elif owner in PERSONAL_OWNER_IDS:
        bucket = "personal_manual"
    elif collaborative:
        bucket = "collaborative_or_friend"
    elif "high_overlap_with_listens" in reasons:
        bucket = "external_high_overlap"
    elif "spotify_owned" in reasons or "generated_or_editorial_name" in reasons or "high_follower_external" in reasons:
        bucket = "external_weak_context"
    elif "album_or_soundtrack_name" in reasons or "short_albumish_playlist" in reasons:
        bucket = "external_album_or_collection"
    else:
        bucket = "external_unknown"

    return bucket, BUCKET_WEIGHTS[bucket], ";".join(reasons)
