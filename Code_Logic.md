# Snapchat Metadata Tool — Code Logic Walkthrough

## 1. Imports / Setup

```python
import os
import json
import shutil
import subprocess
from datetime import datetime
import pytz
from tzlocal import get_localzone_name
from timezonefinder import TimezoneFinder
from pathlib import Path
from PIL import Image
from typing import Optional
```

**What these are for:**

- `os`, `shutil`, `Path` → moving/copying/renaming files on disk.
- `json` → reading Snapchat’s `memories_history.json`.
- `subprocess` → calling command-line tools (`ffmpeg`, `ffprobe`, `exiftool`).
- `datetime`, `pytz`, `timezonefinder`, `tzlocal` → all the timezone math and DST handling.
- `PIL.Image` → adding caption overlays onto images.
- `typing.Optional` → (not really used heavily here, just for type hints).

---

## 2. Load memories metadata

```python
with open("input/memories_history.json", "r", encoding="utf-8") as f:
    metadata = json.load(f)["Saved Media"]

tf = TimezoneFinder()
system_timezone = get_localzone_name()
```

**What’s happening:**

- Snapchat gives you a big JSON file with data for every Memory you ever saved.
- This loads that file, takes the `"Saved Media"` list, and stores it in memory as `metadata`.
- It also create:
  - `tf`: a `TimezoneFinder` function will be used to find the timezone from GPS lat/lon.
  - `system_timezone`: whatever timezone the computer is currently in.

Why this matters:  
This script has two concepts of “local time”:

1. **Location time** (the timezone at the GPS coordinates where the snap happened)
2. **System time** (your current timezone right now)

I build two different output folders using those two interpretations: the **Location time** folder is compatible with Microsft File Explorer and the **System time** folder tuned for iCloud Photos display.

    Having two seperate folders matters because iCloud Photos already converts the files time based off the GPS timezone by itself. So, using files with already converted timezones will make timestamps appear double-converted in Apple Photos.

---

## 3. Helper: `get_metadata(filename)`

```python
def get_metadata(filename):
    for m in metadata:
        if "mid=" in m["Download Link"]:
            mid = m["Download Link"].split("mid=")[1].split("&")[0]
            if mid in filename:
                return m
    return None
```

**Purpose:**

- Every file from Memories has a long ID in its filename, like `...-main.mp4`.
- That same ID shows up in the JSON (`"mid=ABC123..."` in `Download Link`).
- This function finds the JSON row that belongs to a given file on disk.

**Why:**  
We need that row because it contains:

- The UTC timestamp for the snap (`"Date": "2023-05-21 22:01:59 UTC"`)
- The GPS coordinates (`"Location": "Latitude, Longitude: 40.2616, -74.372795"`)  
  Those two things drive filename timestamp, metadata timestamp, GPS tagging, DST math, etc.

---

## 4. Helper: `adjust_time(utc_time, gps_coords, target_tz=None)`

```python
def adjust_time(utc_time, gps_coords, target_tz=None):
    try:
        lat, lon = map(float, gps_coords.split(", "))
        utc_dt = datetime.strptime(utc_time, "%Y-%m-%d %H:%M:%S UTC").replace(
            tzinfo=pytz.utc
        )

        if (lat, lon) == (0.0, 0.0):
            tz_obj = pytz.timezone(target_tz) if target_tz else pytz.utc
            return utc_dt.astimezone(tz_obj).strftime("%Y:%m:%d %H:%M:%S"), None

        gps_tz = tf.timezone_at(lng=lon, lat=lat)
        local_tz = pytz.timezone(gps_tz)
        tz_obj = pytz.timezone(target_tz) if target_tz else local_tz

        return utc_dt.astimezone(tz_obj).strftime("%Y:%m:%d %H:%M:%S"), tz_obj.zone
    except:
        dt = datetime.strptime(utc_time, "%Y-%m-%d %H:%M:%S UTC")
        return dt.strftime("%Y:%m:%d %H:%M:%S"), None
```

**What this does:**

1. Takes the UTC timestamp string from Snapchat + GPS coordinates.
2. Figures out what timezone that GPS point is in.
3. Converts the UTC time → that local time (calculating DST if that place observes DST).
4. Returns:
   - the formatted local time string like `"2023:05:21 18:01:59"`
   - the timezone name we actually used.

**Special case:**  
If GPS is `0.0, 0.0` (which means Snapchat had no real location for that snap), then we skip all the timezone math and either:

- fall back to UTC, OR
- fall back to a provided `target_tz` manually.

---

## 5. Helper: `format_dms(lat, lon)`

```python
def format_dms(lat, lon):
    def dms(deg):
        d = int(deg)
        m = int((abs(deg) - abs(d)) * 60)
        s = (abs(deg) - abs(d) - m / 60) * 3600
        return f"{abs(d)} deg {m}' {s:.2f}\""

    lat_dms = f"{dms(float(lat))} N" if float(lat) >= 0 else f"{dms(float(lat))} S"
    lon_dms = f"{dms(float(lon))} E" if float(lon) >= 0 else f"{dms(float(lon))} W"
    return f"{lat_dms}, {lon_dms}"
```

**What this does:**

- Converts decimal GPS like `40.748615, -74.02536` into human-friendly DMS format (`40 deg 44' 55.01" N, 74 deg 1' 31.30" W`).
- iCloud Photos / Apple Maps likes GPS tags to look “normal”, and File Explorer sometimes shows them if they're written in common EXIF fields.

So this is mostly cosmetic metadata for the GPSCoordinates tag.

---

## 6. Core metadata writer: `update_metadata(...)`

```python
def update_metadata(file_path, date_time, gps_coords=None, only_modified=False):
    current_time = datetime.now().strftime("%Y:%m:%d %H:%M:%S")

    if not only_modified:
        clean_cmd = [
            "exiftool",
            "-overwrite_original",
            "-tagsFromFile", "@",
            "-All:Time*=",
            "-AllDates=",
            "-MediaCreateDate=",
            "-MediaModifyDate=",
            "-CreateDate=",
            "-ModifyDate=",
            "-TrackCreateDate=",
            "-TrackModifyDate=",
            "-QuickTime:CreateDate=",
            "-QuickTime:ModifyDate=",
            "-UserData:DateTimeOriginal=",
            "-XMP:DateTimeOriginal=",
            "-XMP:CreateDate=",
            "-XMP:ModifyDate=",
            "-XMP-exif:DateTimeOriginal=",
            "-XMP-pdf:CreationDate=",
            "-DateTimeOriginal=",
            "-DateCreated=",
            "-DateTimeDigitized=",
            "-XPKeywords=",
            "-XPComment=",
            "-XPSubject=",
            "-XPTitle=",
            "-Microsoft:DateAcquired=",
            str(file_path),
        ]
        subprocess.run(clean_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
```

First half = CLEAN PHASE:

- We wipe basically every time-related field and “Windows weird fields” out of the file.
- This prevents old Snapchat/phone metadata from fighting us later and confusing iCloud or File Explorer.

Then we set new values:

```python
    set_cmd = [
        "exiftool",
        "-overwrite_original",
        f"-FileCreateDate={current_time}",
        f"-FileModifyDate={current_time}",
    ]
```

- We force FileCreateDate/FileModifyDate to “now”.
  - Why? Windows Explorer tends to re-sort by those sometimes. Setting them to “now” makes Explorer show that the file was just created and prevents Windows from silently rewriting them.

Then if it's doing a full write (not just touching modified time), we also inject the “true” photo/video time everywhere:

```python
    if not only_modified:
        set_cmd += [
            f"-AllDates={date_time}",
            f"-MediaCreateDate={date_time}",
            f"-MediaModifyDate={date_time}",
            f"-CreateDate={date_time}",
            f"-ModifyDate={date_time}",
            f"-TrackCreateDate={date_time}",
            f"-TrackModifyDate={date_time}",
            f"-QuickTime:CreateDate={date_time}",
            f"-QuickTime:ModifyDate={date_time}",
            f"-UserData:DateTimeOriginal={date_time}",
            f"-XMP:CreateDate={date_time}",
            f"-XMP:ModifyDate={date_time}",
            f"-XMP:DateCreated={date_time}",
            f"-XMP-exif:DateTimeOriginal={date_time}",
            f"-XMP-pdf:CreationDate={date_time}",
            f"-DateTimeOriginal={date_time}",
            f"-DateTimeDigitized={date_time}",
            f"-Microsoft:DateAcquired={date_time}",
        ]
```

Why so many tags?

- Different apps read different tags:
  - iCloud Photos cares about EXIF DateTimeOriginal / QuickTime:CreateDate.
  - Windows File Explorer sometimes surfaces Microsoft:DateAcquired or DateTimeOriginal in the “Date” column.
- By setting basically all of them to the same timestamp, everything lines up visually in iOS, iCloud web, Windows Explorer, and potentially any other photo application users may use.

Next, we optionally write GPS:

```python
        if gps_coords and gps_coords != "0.0, 0.0":
            lat, lon = gps_coords.split(", ")
            dms = format_dms(float(lat), float(lon))
            set_cmd.extend(
                [
                    f"-GPSLatitude={lat}",
                    f"-GPSLongitude={lon}",
                    "-GPSLatitudeRef=N" if float(lat) > 0 else "-GPSLatitudeRef=S",
                    "-GPSLongitudeRef=E" if float(lon) > 0 else "-GPSLongitudeRef=W",
                    f"-XMP-exif:GPSLatitude={lat}",
                    f"-XMP-exif:GPSLongitude={lon}",
                    f"-Keys:GPSCoordinates={dms}",
                ]
            )
```

- This writes latitude/longitude into standard EXIF GPS fields and also into some Apple-friendly/XMP fields.
- iOS uses this to show map pins in Photos.
- Windows sometimes surfaces these in Details view.

Finally:

```python
    set_cmd.append(str(file_path))
    subprocess.run(set_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        timestamp = datetime.strptime(date_time, "%Y:%m:%d %H:%M:%S").timestamp()
        os.utime(file_path, (timestamp, timestamp))
    except:
        pass
```

- Then we run exiftool.
- Then we also force the filesystem-level modified/accessed time (`os.utime`) to match the real snap time.

---

## 7. Filename generator: `generate_filename(...)`

```python
def generate_filename(date_time_str):
    dt_obj = datetime.strptime(date_time_str, "%Y:%m:%d %H:%M:%S")
    return dt_obj.strftime("%Y-%m-%d_%H-%M-%S")
```

- Converts the final timestamp (already adjusted to the correct timezone) into a clean, readable filename.
- Filenames follow the format `YYYY-MM-DD_HH-MM-SS`.
- Keeps filenames concise and consistent across all media types.

**Why timestamp filenames:**

- Ensures unique, chronological sorting.
- Makes files easily readable and organized by time.
- Matches the intended output convention where each filename directly reflects the snap’s actual datetime.

---

## 8. Overlay helpers

### `apply_overlay_image(...)`

```python
def apply_overlay_image(base_path, overlay_path, output_path):
    try:
        base = Image.open(base_path).convert("RGBA")
        overlay = Image.open(overlay_path).convert("RGBA")
        overlay = overlay.resize(base.size, Image.LANCZOS)
        base.paste(overlay, (0, 0), overlay)
        base.convert("RGB").save(output_path, "JPEG", quality=95)
        return True
    except Exception as e:
        print(f"   Overlay image failed for {base_path.name}: {e}")
        return False
```

How this works:

- Opens the original snap frame.
- Opens the overlay PNG (caption, stickers, etc.).
- Resizes overlay to match.
- Pastes it on top, respecting transparency.
- Saves as a new JPEG with `_overlay` in the name.

After creating the overlay version, we immediately call `update_metadata()` on the new file so the overlay version keeps the exact same timestamp and GPS as the non-overlay original.

---

### `apply_overlay_video(...)` (dispatcher)

```python
def apply_overlay_video(base_path, overlay_path, output_path):
    try:
        # probe resolution with ffprobe
        ...
        if width > height:
            return apply_overlay_landscape(...)
        else:
            return apply_overlay_portrait(...)
    except Exception:
        return False
```

- Uses `ffprobe` to detect orientation.
- If landscape: uses one overlay pipeline.
- If portrait: uses another.

Why two code paths?  
Because Snapchat memories vs ones saved from TikTok / snapcam vs screen-recorded videos can have different rotation metadata. We learned we needed different `ffmpeg` chains for landscape vs portrait to keep orientation correct and line up the caption layer.

---

### `apply_overlay_landscape(...)`

```python
def apply_overlay_landscape(base_path, overlay_path, output_path):
    width, height = get_video_resolution(base_path)

    subprocess.run([
        "ffmpeg",
        "-i", base,
        "-i", overlay,
        "-filter_complex",
        "[0:v]transpose=2[vid];[1:v]transpose=2,scale=WIDTH:HEIGHT[ovr];[vid][ovr]overlay=0:0",
        "-map_metadata", "-1",
        "-metadata:s:v", "rotate=0",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-y", output
    ])
```

What it’s doing:

- Rotate both the base video and overlay into an upright orientation with `transpose=2`.
- Scale the overlay to match the video.
- Burn the overlay onto the video with `overlay=0:0`.
- Force rotation metadata to 0 so iOS doesn’t double-rotate it later.
- Keep audio untouched.
- Write a new H.264 MP4 that iCloud Photos will accept.

Then we return True if we actually created the file.

---

### `apply_overlay_portrait(...)`

```python
def apply_overlay_portrait(base_path, overlay_path, output_path):
    # 1. use ffprobe to get width/height
    # 2. ffmpeg scale overlay PNG to match video size (writes a temp _resized_overlay.png)
    # 3. ffmpeg overlay=0:0 base + resized overlay → output_path
    # 4. delete temp resized overlay
```

Same idea, but simpler filter chain.  
Portrait snaps didn’t need the transpose trick, but they did need a resize so the overlay matches exactly.

Again: after creating this `_overlay.mp4`, the script later calls `update_metadata()` on it to clone the timestamp/GPS from the original clip.

---

## 9. `merge_video_clips(...)`

```python
def merge_video_clips(groups, input_dir, output_dir_location, output_dir_system):
    used_filenames = {}

    for group in groups:
        first_clip = group[0]
        first_meta = get_metadata(first_clip.name)
        date_utc = first_meta["Date"]
        gps_coords = ...
        ...
        # figure out two timestamps:
        # 1. gps_local_str  (local time at the snap location)
        # 2. system_time_str (local time in current system timezone)

        base_filename = generate_filename(gps_local_str)
        # handle duplicate filenames by appending _2, _3, etc.
        filename = maybe_add_counter(...)

        merged_path_location = output_dir_location / f"{filename}.mp4"
        merged_path_system   = output_dir_system   / f"{filename}.mp4"

        # Build a concat list file for ffmpeg
        # ffmpeg -f concat -i temp_inputs.txt -c copy merged.mp4
        # (This just stitches the short clips together with no re-encode.)
```

Why we’re merging:

- Snapchat often saves long memories as multiple ~10 second clips that are really one continuous moment.
- We detect “these belong together” if:
  - time gap between clips is ~9–11 seconds, AND
  - GPS is exactly the same.
- We then join them into one merged file.

Then we do two outputs per merged group:

1. **memories location time** version

   - Timestamp = GPS-local time (`gps_local_str`)
   - Metadata GPS = the snap’s GPS
   - Console prints:
     - Location
     - Timezone used
     - Final datetime
     - “Added to → memories location time”
   - After export, we also look for an overlay PNG.
     - If there is one, we create `filename_overlay.mp4`, apply the overlay, then run `update_metadata()` on it with the SAME timestamp/GPS.

2. **memories system time** version
   - Timestamp = system-local time (`system_time_str`)
   - This is the “iCloud Photos friendly” version.
   - We still write GPS so Maps still works in Photos.
   - We print a second block to console that clearly shows that this is the “system timezone” copy.
   - We also generate an overlay version here if applicable, and run `update_metadata()` again with the system time value.

---

## 10. `process_memories()`

```python
def process_memories():
    input_dir = Path("input/memories")
    output_dir_mem = Path("output/memories location time")
    output_dir_system = Path("output/memories system time")
    output_dir_mem.mkdir(...)
    output_dir_system.mkdir(...)
```

- Walk through everything in `input/memories`.
- Split them into two categories:
  1. Groups of video clips that should be merged.
  2. Individual items (single video not merged OR single photo).
- For each memory, create **two outputs**:
  - One in “memories location time”
  - One in “memories system time”
- Apply overlays if available.
- Clean + rewrite all metadata.

### Step A: Find and group mergeable videos

```python
    all_videos = []
    for file in sorted(input_dir.iterdir()):
        if file.suffix.lower() == ".mp4" and "-main" in file.name:
            meta = get_metadata(file.name)
            if meta:
                all_videos.append((file, meta))

    all_videos.sort(key=lambda x: x[1]["Date"])
    groups = []
    current_group = []

    for i, (file, meta) in enumerate(all_videos):
        if not current_group:
            current_group.append(file)
            continue

        prev_meta = get_metadata(current_group[-1].name)

        prev_dt = parse(prev_meta["Date"] in UTC)
        curr_dt = parse(meta["Date"] in UTC)
        time_diff = |curr - prev| in seconds

        same_gps = prev_meta["Location"] == meta["Location"]

        if same_gps and 9 <= time_diff <= 11:
            # same moment, same place, less than ~10s apart → same group
            current_group.append(file)
        else:
            groups.append(current_group)
            current_group = [file]

    if current_group:
        groups.append(current_group)
```

Now `groups` is a list like:

- `[ [clip1, clip2, clip3], [clip4], [clip5, clip6], [clip7], ... ]`

### Step B: Merge multi-clip groups

```python
    used_filenames = merge_video_clips(
        [g for g in groups if len(g) > 1],
        input_dir,
        output_dir_mem,
        output_dir_system
    )
```

- We call `merge_video_clips()` on only the groups that have more than one file.
- We also get back a dict of filenames we already used so we can avoid collisions later.

### Step C: Handle everything else (singles)

```python
    to_keep = [g[0] for g in groups if len(g) == 1]
    existing_filenames = used_filenames.copy()

    for file in sorted(input_dir.iterdir()):
        if "-main" not in file.name:
            continue
        if file.suffix.lower() == ".mp4" and file not in to_keep:
            continue  # already merged above
```

Now for each individual memory (photo OR solo video):

1. Look up metadata with `get_metadata`.
2. Grab UTC timestamp and GPS.
3. Print a console header like:

   ```text
   →  Processing memories: 2023-05-19_CF68C...-main.mp4
       Location → (lat, lon)
       Timezone used → America/New_York
   ```

   OR:

   ```text
       Location → none found
       System timezone used → America/New_York
   ```

4. Convert UTC → local time at GPS (or → system time if no GPS).
5. Build the filename using that local time (including seconds).
6. Handle duplicates (if same timestamp appears twice, add `_2`, `_3`, etc.).
7. Copy the file into:
   - `output/memories location time`
   - `output/memories system time`
8. Run `update_metadata()` on each copy:
   - First copy uses the GPS-local converted time.
   - Second copy uses system timezone time.
   - Both copies get GPS tags if GPS exists.
9. Print nice console lines:

   ```text
   Final datetime → 2023:05:20 01:35:40
   File name updated → 2023-05-20_01-35-40.mp4
   Added to → memories location time
   ```

   then

   ```text
   →  Processing copy 2023-05-20_01-35-40.mp4
      System timezone used → America/New_York
      Final datetime → 2023:05:19 20:35:40
      Added to → memories system time
   ```

10. Check for overlays:
    - We take the _original_ stem (before renaming), replace `-main` with nothing, then look for a corresponding `*-overlay.png` in `input/memories`.
    - If it exists:
      - For images: create a `_overlay.jpg` with `apply_overlay_image()`
      - For videos: create a `_overlay.mp4` with `apply_overlay_video()`
      - Then run `update_metadata()` on that overlay version so it has the same timestamp/GPS as the non-overlay twin.
      - Print “Overlay version added → ...\_overlay.mp4”

---

## 11. Chat media helpers

These are just utilities for dealing with DMs / chat exports.

### `has_video_stream()` / `has_audio_stream()`

```python
def has_video_stream(file_path: Path) -> bool:
    ffprobe ... "v:0"
    return "video" in result

def has_audio_stream(file_path: Path) -> bool:
    ffprobe ... "a:0"
    return "audio" in result
```

- Snapchat chat attachments can be:
  - photos,
  - videos,
  - voice notes,
  - or weird MP4 containers that are just audio.
- We use `ffprobe` to detect if something is actually video+audio, audio-only, etc.  
  That decides whether this should be treated like a video memory or like a voice message.

### `convert_to_mp3(...)`

```python
def convert_to_mp3(input_file: Path, output_file: Path):
    ffmpeg -i input -vn -acodec libmp3lame output.mp3
```

- Voice notes from chat_media often come out as `.mp4` “videos” with no video frames, just audio.
- We turn those into `.mp3` files because that’s more convenient to listen to later.

---

## 12. `process_chat_media()`

```python
def process_chat_media():
    input_dir = Path("input/chat_media")
    output_dir = Path("output/chat media")
    voice_dir = Path("output/chat media voice messages")
    ...
    date_counter = {}
    voice_counter = {}
```

Goal of this function:

- Take exported chat attachments.
- Normalize names.
- Force metadata to a clean date/time (midnight of that day).
- Split them into:
  - regular chat media (pics/videos),
  - voice messages (mp3).
- Keeps a counter so multiple files from the same day don’t have duplicate names

### Loop over every file in `input/chat_media`

We skip:

- Unsupported filetypes
- Thumbnails (filenames containing “thumbnail”)

Then we grab the date from the start of the filename:

```python
date_str = file.name.split("_")[0]
# Example: "2024-03-05_thumbnail~..." -> "2024-03-05"
```

Now we branch:

#### Case A: Images (`.jpg`, `.jpeg`)

```python
date_counter.setdefault(date_str, 0)
date_counter[date_str] += 1
suffix = date_counter[date_str]

new_name = f"{date_str}_chat_media_{suffix}.jpg"
shutil.copy2(file, new_file)

formatted = f"{date_str} 00:00:00" → "YYYY:MM:DD 00:00:00"
update_metadata(new_file, formatted)
```

- We copy the file to `output/chat media/2024-03-05_chat_media_1.jpg`.
- We set all its internal timestamps to that date at 00:00:00 local-style format.
- We print nicely:
  ```text
  →  Processing chat_media: originalFileName.jpg
     Final datetime → 2024:03:05 00:00:00
     File name updated → 2024-03-05_chat_media_1.jpg
     Added to → chat media
  ```

Why 00:00:00?

- Originally I wanted to try and use “12:00 PM" or any time metadata that may be in the chat media files already, but the final code uses 00:00:00 midnight. We standardize the time because the exported data for chats only gave us a date, not a time, so we just pick a neutral consistent time.

#### Case B: Videos and voice notes (`.mp4`)

We first check:

- Does this `.mp4` actually have a video track?
  - If yes → treat it like photo/video media.
- Does it only have audio?
  - If yes → treat it like a voice message.

If it’s a real video:

```python
new_name = f"{date_str}_chat_media_{suffix}.mp4"
copy → output/chat media
update_metadata(new_file, formatted)
print lines
```

If it’s only audio:

```python
new_name = f"{date_str}_voice_message_{suffix}.mp3"
convert_to_mp3(...)
update_metadata(... same formatted date/time ...)
print lines saying "Converted voice message to mp3"
```

The counters `date_counter` and `voice_counter` make sure:

- Multiple chat attachments on the same day become `_1`, `_2`, `_3`, etc.
- Voice messages get their own sequence separate from photos/videos.

End result:

- Clean folder for “chat media” you can browse like a camera roll.
- Clean folder for “chat media voice messages” where voice notes are playable `.mp3` files with consistent naming.

---

## 13. `main()` and script entry

```python
def main():
    process_chat_media()
    process_memories()

if __name__ == "__main__":
    main()
```

This just says:

1. Process chat_media first.
2. Then process memories.
3. Do all the copying, renaming, overlaying, timestamp fixing, GPS tagging, etc.

---

## 14. Putting it all together (big picture)

### What the script actually produces

After running, you get these output folders:

1. `output/chat media/`

   - All pics and videos from chats
   - Named like `YYYY-MM-DD_chat_media_#`
   - Metadata time = that date at a fixed “neutral” time
   - Timestamps rewritten so Windows/iCloud read them consistently

2. `output/chat media voice messages/`

   - All voice notes from chat as `.mp3`
   - Named like `YYYY-MM-DD_voice_message_#`
   - Same timestamp logic

3. `output/memories location time/`

   - Every Memory photo/video
   - Merged videos (when Snapchat split them)
   - Filenames based on the moment’s local time at the GPS location (including seconds)
   - Metadata timestamps set to that GPS-local time
   - GPS embedded so Apple Photos map view works
   - Overlay versions (`*_overlay.jpg` / `*_overlay.mp4`) with identical metadata

4. `output/memories system time/`
   - A second copy of each memory
   - Same filename as above (so both folders match visually)
   - But metadata timestamps are adjusted into your computer’s current timezone instead
     - This is so iCloud Photos shows the right “top of screen” time when you scroll your camera roll on your phone now.
   - GPS still embedded
   - Overlay versions also created here, and also metadata-aligned

### Why two “memories” folders?

- File Explorer and general archival:  
  You usually care about “what time was it in _that_ place?”  
  That’s `memories location time`.

- iCloud Photos / iPhone timeline:  
  You care about Photos showing the “correct” local display time when you scroll your camera roll on your phone now.  
  That’s `memories system time`.

This dual-output logic is exactly the final design direction:

- clean metadata
- fix timezones including DST
- preserve GPS
- generate overlay variants right next to originals
- avoid duplicates with counters
- keep Windows + iCloud both happy at the same time.

---

## 15. Summary

1. Read Snapchat’s big JSON export.
2. For each memory:

   - Find its UTC timestamp and GPS.
   - Convert that UTC time to:
     - the GPS location's local timezone,
     - AND to your current system timezone.
   - Create output copies in two folders, one per interpretation.
   - Clean and rewrite ALL metadata timestamps and GPS tags.
   - Give the files readable, chronological names.
   - If an overlay version exists, burn it in and copy the same metadata.

3. For chat_media:
   - Rename everything to `YYYY-MM-DD_chat_media_#`.
   - Standardize the time to midnight that day.
   - Convert voice notes into mp3s.
   - Clean and rewrite timestamps.

So basically:  
The script rebuilds your Snapchat export into a legit camera roll / timeline that looks normal in Windows and iCloud Photos, with correct time, correct GPS, captions burned in when available, and voice messages handled cleanly.
