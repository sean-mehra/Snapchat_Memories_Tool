import os
import sys
import json
import re
import shutil
import subprocess
from datetime import datetime
import pytz
from tzlocal import get_localzone_name
from timezonefinder import TimezoneFinder
from pathlib import Path
from PIL import Image
from typing import Optional

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# Load metadata
with open("input/memories_history.json", "r", encoding="utf-8") as f:
    metadata = json.load(f)["Saved Media"]

tf = TimezoneFinder()
system_timezone = get_localzone_name()

# Load chat history metadata if available
chat_metadata_map = {}
chat_history_list = []
chat_history_path = Path("input/chat_history.json")
if chat_history_path.exists():
    try:
        with open(chat_history_path, "r", encoding="utf-8") as f:
            chat_data = json.load(f)
        for conv_key, messages in chat_data.items():
            for msg in messages:
                chat_entry = {
                    "Created": msg.get("Created"),
                    "From": msg.get("From"),
                    "IsSender": msg.get("IsSender"),
                    "Title": msg.get("Conversation Title"),
                    "Media Type": msg.get("Media Type"),
                }
                chat_history_list.append(chat_entry)
                media_id_str = msg.get("Media IDs", "").strip()
                if media_id_str:
                    for mid in re.split(r"[,|;]", media_id_str):
                        mid = mid.strip()
                        if mid:
                            chat_metadata_map[mid] = chat_entry
    except Exception as e:
        print(f"Warning: Could not load chat_history.json: {e}")

# Load snap history metadata if available
snap_metadata_list = []
snap_history_path = Path("input/snap_history.json")
if snap_history_path.exists():
    try:
        with open(snap_history_path, "r", encoding="utf-8") as f:
            snap_data = json.load(f)
        for conv_key, snaps in snap_data.items():
            for s in snaps:
                snap_metadata_list.append(
                    {
                        "Created": s.get("Created"),
                        "From": s.get("From"),
                        "IsSender": s.get("IsSender"),
                        "Title": s.get("Conversation Title"),
                        "Media Type": s.get("Media Type"),
                        "Microseconds": s.get("Created(microseconds)"),
                    }
                )
    except Exception as e:
        print(f"Warning: Could not load snap_history.json: {e}")

_filename_to_meta_cache = {}


def _build_metadata_cache():
    global _filename_to_meta_cache
    if _filename_to_meta_cache:
        return

    from collections import defaultdict

    mid_map = {}
    meta_by_date = defaultdict(list)

    for m in metadata:
        link = m.get("Download Link", "") or m.get("Media Download Url", "")
        if "mid=" in link:
            mid = link.split("mid=")[1].split("&")[0]
            mid_map[mid] = m
        date_str = m.get("Date", "").split(" ")[0]
        if date_str:
            meta_by_date[date_str].append(m)

    mem_dir = Path("input/memories")
    if not mem_dir.exists():
        return

    all_main_files = sorted([f for f in mem_dir.iterdir() if "-main" in f.name])

    for f in all_main_files:
        matched = None
        for mid, m in mid_map.items():
            if mid in f.name:
                matched = m
                break

        if not matched:
            date_str = f.name.split("_")[0]
            same_date_files = [x for x in all_main_files if x.name.startswith(date_str)]
            same_date_meta = meta_by_date.get(date_str, [])

            if len(same_date_meta) == 1:
                matched = same_date_meta[0]
            elif len(same_date_meta) > 1:
                try:
                    idx = same_date_files.index(f)
                    matched = same_date_meta[idx if idx < len(same_date_meta) else 0]
                except ValueError:
                    matched = same_date_meta[0]

        if matched:
            _filename_to_meta_cache[f.name] = matched


def get_metadata(filename):
    _build_metadata_cache()
    if filename in _filename_to_meta_cache:
        return _filename_to_meta_cache[filename]

    for m in metadata:
        link = m.get("Download Link", "") or m.get("Media Download Url", "")
        if "mid=" in link:
            mid = link.split("mid=")[1].split("&")[0]
            if mid in filename:
                return m

    date_str = filename.split("_")[0]
    for m in metadata:
        if m.get("Date", "").startswith(date_str):
            return m

    return None



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


def format_dms(lat, lon):
    def dms(deg):
        d = int(deg)
        m = int((abs(deg) - abs(d)) * 60)
        s = (abs(deg) - abs(d) - m / 60) * 3600
        return f"{abs(d)} deg {m}' {s:.2f}\""

    lat_dms = f"{dms(float(lat))} N" if float(lat) >= 0 else f"{dms(float(lat))} S"
    lon_dms = f"{dms(float(lon))} E" if float(lon) >= 0 else f"{dms(float(lon))} W"
    return f"{lat_dms}, {lon_dms}"


class ExifToolRunner:
    def __init__(self):
        self.proc = None

    def _start(self):
        if self.proc is None or self.proc.poll() is not None:
            try:
                self.proc = subprocess.Popen(
                    ["exiftool", "-stay_open", "True", "-@", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
            except Exception:
                self.proc = None

    def execute(self, args):
        self._start()
        if not self.proc:
            subprocess.run(["exiftool"] + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return

        try:
            for arg in args:
                self.proc.stdin.write(f"{arg}\n")
            self.proc.stdin.write("-execute\n")
            self.proc.stdin.flush()

            while True:
                line = self.proc.stdout.readline()
                if not line or "{ready}" in line:
                    break
        except Exception:
            self.proc = None
            subprocess.run(["exiftool"] + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write("-stay_open\nFalse\n-execute\n")
                self.proc.stdin.flush()
                self.proc.communicate(timeout=2)
            except Exception:
                pass
            self.proc = None


_exiftool_runner = ExifToolRunner()


def update_metadata(file_path, date_time, gps_coords=None, only_modified=False, tags=None):
    if not tags:
        tags = ["Snapchat"]
    elif "Snapchat" not in tags:
        tags = ["Snapchat"] + list(tags)

    current_time = datetime.now().strftime("%Y:%m:%d %H:%M:%S")
    cmd = ["exiftool", "-overwrite_original"]

    if not only_modified:
        cmd += [
            "-tagsFromFile",
            "@",
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
        ]

    cmd += [
        f"-FileCreateDate={current_time}",
        f"-FileModifyDate={current_time}",
    ]

    if not only_modified:
        cmd += [
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

        for tag in tags:
            cmd.append(f"-Keywords={tag}")
            cmd.append(f"-Subject={tag}")
            cmd.append(f"-XMP-dc:Subject={tag}")

        cmd.append(f"-XPKeywords={'; '.join(tags)}")
        cmd.append(f"-Keys:Keywords={', '.join(tags)}")
        cmd.append(f"-UserData:Keywords={', '.join(tags)}")
        cmd.append(f"-ItemList:Keyword={', '.join(tags)}")

        if len(tags) > 1:
            cmd.append(f"-XMP-lr:HierarchicalSubject={'|'.join(tags)}")

        if gps_coords and gps_coords != "0.0, 0.0":
            lat, lon = gps_coords.split(", ")
            dms = format_dms(float(lat), float(lon))
            cmd.extend(
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

    cmd.append(str(file_path))
    _exiftool_runner.execute(cmd[1:])

    try:
        timestamp = datetime.strptime(date_time, "%Y:%m:%d %H:%M:%S").timestamp()
        os.utime(file_path, (timestamp, timestamp))
    except:
        pass


def generate_filename(date_time_str):
    dt_obj = datetime.strptime(date_time_str, "%Y:%m:%d %H:%M:%S")
    return dt_obj.strftime("%Y-%m-%d_%H-%M-%S")


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


def apply_overlay_video(base_path, overlay_path, output_path):
    try:
        # Get video resolution
        probe_cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(base_path)
        ]
        result = subprocess.run(probe_cmd, capture_output=True, text=True)

        lines = result.stdout.strip().splitlines()
        if not lines:
            return False

        parts = [p for p in lines[0].split(",") if p.strip()]
        if len(parts) < 2:
            return False

        width = int(parts[0])
        height = int(parts[1])

        if width > height:
            return apply_overlay_landscape(base_path, overlay_path, output_path)
        else:
            return apply_overlay_portrait(base_path, overlay_path, output_path)

    except Exception:
        return False


def get_video_resolution(video_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    info = json.loads(result.stdout)
    width = info['streams'][0]['width']
    height = info['streams'][0]['height']
    return width, height


def apply_overlay_landscape(base_path, overlay_path, output_path):
    try:
        width, height = get_video_resolution(base_path)

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-i", str(base_path),
                "-i", str(overlay_path),
                "-filter_complex",
                f"[0:v]transpose=2[vid];[1:v]transpose=2,scale={width}:{height}[ovr];[vid][ovr]overlay=0:0",
                "-map_metadata", "-1",
                "-metadata:s:v", "rotate=0",
                "-c:v", "libx264",
                "-crf", "18",
                "-preset", "fast",
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(output_path)
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return output_path.exists()

    except Exception as e:
        print(f"   Overlay video failed (landscape) for {base_path.name}: {e}")
        return False


def apply_overlay_portrait(base_path, overlay_path, output_path):
    try:
        # Get resolution again
        probe_cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(base_path)
        ]
        result = subprocess.run(probe_cmd, capture_output=True, text=True)
        lines = result.stdout.strip().splitlines()
        width, height = lines[0].split(",")

        resized_overlay = Path(str(output_path).replace(".mp4", "_resized_overlay.png"))

        # Resize overlay to match video size
        resize_cmd = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-i", str(overlay_path),
            "-vf", f"scale={width}:{height}",
            str(resized_overlay)
        ]
        subprocess.run(resize_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Apply overlay directly
        overlay_cmd = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-i", str(base_path),
            "-i", str(resized_overlay),
            "-filter_complex", "overlay=0:0",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path)
        ]
        subprocess.run(overlay_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if resized_overlay.exists():
            os.remove(resized_overlay)

        return output_path.exists()

    except Exception as e:
        print(f"   Overlay video failed (portrait) for {base_path.name}: {e}")
        return False


def concat_video_files(clips: list, output_path: Path) -> bool:
    concat_list = output_path.parent / f"temp_concat_{output_path.stem}.txt"
    try:
        with open(concat_list, "w", encoding="utf-8") as f:
            for clip in clips:
                f.write(f"file '{clip.resolve().as_posix()}'\n")

        # Try re-encoding first to ensure clean timestamps and prevent video corruption
        res = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "fast",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        if not output_path.exists() or output_path.stat().st_size == 0:
            # Fallback to copy stream if re-encoding fails
            res = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-nostdin",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_list),
                    "-c",
                    "copy",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
            )
        if not output_path.exists() or output_path.stat().st_size == 0:
            print(f"   [Error] ffmpeg concat failed: {res.stderr.strip()}")
            return False
        return True
    except Exception as e:
        print(f"   [Error] ffmpeg concat exception: {e}")
        return False
    finally:
        if concat_list.exists():
            try:
                concat_list.unlink()
            except Exception:
                pass


def merge_video_clips(groups, input_dir, output_dir_location, output_dir_system):
    used_filenames = {}

    for group in groups:
        first_clip = group[0]
        first_meta = get_metadata(first_clip.name)
        date_utc = first_meta["Date"]
        gps_coords = None
        if (
            "Location" in first_meta
            and "Latitude, Longitude: " in first_meta["Location"]
        ):
            gps_coords = first_meta["Location"].split(": ")[1]

        if gps_coords and gps_coords != "0.0, 0.0":
            gps_local_str, gps_tz = adjust_time(date_utc, gps_coords)
        else:
            dt = datetime.strptime(date_utc, "%Y-%m-%d %H:%M:%S UTC")
            gps_local_str = (
                pytz.utc.localize(dt)
                .astimezone(pytz.timezone(system_timezone))
                .strftime("%Y:%m:%d %H:%M:%S")
            )
            gps_tz = None

        dt = datetime.strptime(date_utc, "%Y-%m-%d %H:%M:%S UTC")
        system_time_str = pytz.utc.localize(dt).astimezone(pytz.timezone(system_timezone)).strftime("%Y:%m:%d %H:%M:%S")

        base_filename = generate_filename(gps_local_str)
        count = used_filenames.get(base_filename, 0) + 1
        used_filenames[base_filename] = count
        filename = base_filename if count == 1 else f"{base_filename}_{count}"

        merged_path_location = output_dir_location / f"{filename}.mp4"
        merged_path_system = output_dir_system / f"{filename}.mp4"

        concat_video_files(group, merged_path_location)
        update_metadata(merged_path_location, gps_local_str, gps_coords)
        print(f"   File name updated → {filename}.mp4")
        print(f"   Added to → memories location time")

        overlay_name = first_clip.stem.split("-main")[0] + "-overlay.png"
        overlay_path = input_dir / overlay_name

        if overlay_path.exists():
            overlay_output_location = output_dir_location / f"{filename}_overlay.mp4"
            if apply_overlay_video(merged_path_location, overlay_path, overlay_output_location):
                update_metadata(overlay_output_location, gps_local_str, gps_coords)
                print(f"   Overlay version added → {overlay_output_location.name}")

        concat_video_files(group, merged_path_system)
        update_metadata(merged_path_system, system_time_str, gps_coords)
        print(f"\n→  Processing copy {filename}.mp4")
        print(f"   System timezone used → {system_timezone}")
        print(f"   Final datetime → {system_time_str}")
        print(f"   Added to → memories system time")

        concat_list.unlink()

        if overlay_path.exists():
            overlay_output_system = output_dir_system / f"{filename}_overlay.mp4"
            if apply_overlay_video(merged_path_system, overlay_path, overlay_output_system):
                update_metadata(overlay_output_system, system_time_str, gps_coords)
                print(f"   Overlay version added → {overlay_output_system.name}")

    return used_filenames


def process_memories(): 
    input_dir = Path("input/memories")
    output_dir_mem = Path("output/memories location time")
    output_dir_system = Path("output/memories system time")
    output_dir_mem.mkdir(parents=True, exist_ok=True)
    output_dir_system.mkdir(parents=True, exist_ok=True)

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
        prev_dt = datetime.strptime(prev_meta["Date"], "%Y-%m-%d %H:%M:%S UTC")
        curr_dt = datetime.strptime(meta["Date"], "%Y-%m-%d %H:%M:%S UTC")
        time_diff = abs((curr_dt - prev_dt).total_seconds())
        same_gps = (
            "Location" in meta
            and "Location" in prev_meta
            and meta["Location"] == prev_meta["Location"]
        )
        if same_gps and 9 <= time_diff <= 11:
            current_group.append(file)
        else:
            groups.append(current_group)
            current_group = [file]
    if current_group:
        groups.append(current_group)

    used_filenames = merge_video_clips(
        [g for g in groups if len(g) > 1], input_dir, output_dir_mem, output_dir_system
    )

    to_keep = [g[0] for g in groups if len(g) == 1]
    existing_filenames = used_filenames.copy()

    for file in sorted(input_dir.iterdir()):
        if "-main" not in file.name:
            continue
        if file.suffix.lower() == ".mp4" and file not in to_keep:
            continue

        meta = get_metadata(file.name)
        if not meta:
            continue

        date_utc = meta["Date"]
        gps_coords = None
        if "Location" in meta and "Latitude, Longitude: " in meta["Location"]:
            gps_coords = meta["Location"].split(": ")[1]

        print(f"\n→  Processing memories: {file.name}")

        # GPS-local time for memories
        if gps_coords and gps_coords != "0.0, 0.0":
            date_time, tz_used = adjust_time(date_utc, gps_coords)
            print(f"   Location → ({gps_coords})")
            print(f"   Timezone used → {tz_used}")
        else:
            dt = datetime.strptime(date_utc, "%Y-%m-%d %H:%M:%S UTC")
            date_time = (
                pytz.utc.localize(dt)
                .astimezone(pytz.timezone(system_timezone))
                .strftime("%Y:%m:%d %H:%M:%S")
            )
            print("   Location → none found")
            print(f"   System timezone used → {system_timezone}")

        ext = file.suffix.lower()
        base_filename = generate_filename(date_time)

        # Add counter if filename already used
        count = existing_filenames.get(base_filename, 0) + 1
        existing_filenames[base_filename] = count
        filename = base_filename if count == 1 else f"{base_filename}_{count}"

        out_mem = output_dir_mem / f"{filename}{ext}"
        shutil.copy2(file, out_mem)
        update_metadata(out_mem, date_time, gps_coords)

        print(f"   Final datetime → {date_time}")
        print(f"   File name updated → {filename}{ext}")
        print(f"   Added to → memories location time")

        # Apply overlay if applicable — memories
        original_stem = file.stem.replace("-main", "")
        overlay_input = input_dir / f"{original_stem}-overlay.png"

        if ext in [".jpg", ".jpeg"] and overlay_input.exists():
            overlay_out_mem = output_dir_mem / f"{filename}_overlay.jpg"
            if apply_overlay_image(out_mem, overlay_input, overlay_out_mem):
                update_metadata(overlay_out_mem, date_time, gps_coords)
                print(f"   Overlay version added → {filename}_overlay.jpg")

        elif ext == ".mp4" and overlay_input.exists():
            overlay_out_mem = output_dir_mem / f"{filename}_overlay.mp4"
            if apply_overlay_video(out_mem, overlay_input, overlay_out_mem):
                update_metadata(overlay_out_mem, date_time, gps_coords)
                print(f"   Overlay version added → {filename}_overlay.mp4")

        # ----- OUTPUT FOR MEMORIES-SYSTEM -----
        out_system = output_dir_system / f"{filename}{ext}"
        shutil.copy2(file, out_system)
        dt = datetime.strptime(date_utc, "%Y-%m-%d %H:%M:%S UTC")
        system_time = (
            pytz.utc.localize(dt)
            .astimezone(pytz.timezone(system_timezone))
            .strftime("%Y:%m:%d %H:%M:%S")
        )
        update_metadata(out_system, system_time, gps_coords)

        print(f"\n→  Processing copy {filename}{ext}")
        print(f"   System timezone used → {system_timezone}")
        print(f"   Final datetime → {system_time}")
        print(f"   Added to → memories system time")

        # Apply overlay if applicable — system
        if ext in [".jpg", ".jpeg"] and overlay_input.exists():
            overlay_out_system = output_dir_system / f"{filename}_overlay.jpg"
            if apply_overlay_image(out_system, overlay_input, overlay_out_system):
                update_metadata(overlay_out_system, system_time, gps_coords)
                print(f"   Overlay version added → {filename}_overlay.jpg")

        elif ext == ".mp4" and overlay_input.exists():
            overlay_out_system = output_dir_system / f"{filename}_overlay.mp4"
            if apply_overlay_video(out_system, overlay_input, overlay_out_system):
                update_metadata(overlay_out_system, system_time, gps_coords)
                print(f"   Overlay version added → {filename}_overlay.mp4")


# Detect if file has a video stream
def has_video_stream(file_path: Path) -> bool:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type",
            "-of", "default=nw=1:nk=1",
            str(file_path)
        ], capture_output=True, text=True)
        return "video" in result.stdout.strip()
    except Exception:
        return False
    

# Detect if file has an audio stream
def has_audio_stream(file_path: Path) -> bool:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "default=nw=1:nk=1",
            str(file_path)
        ], capture_output=True, text=True)
        return "audio" in result.stdout.strip()
    except Exception:
        return False


def convert_to_mp3(input_file: Path, output_file: Path):
    try:
        subprocess.run([
            "ffmpeg", "-y", "-nostdin", "-i", str(input_file), "-vn", "-acodec", "libmp3lame", str(output_file)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_file.exists()
    except Exception:
        return False


def parse_utc_timestamp(date_str: str) -> Optional[datetime]:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S UTC").replace(
            tzinfo=pytz.utc
        )
    except Exception:
        return None


def get_video_creation_time(file_path: Path) -> Optional[datetime]:
    if file_path.suffix.lower() != ".mp4":
        return None
    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(file_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            tags = data.get("format", {}).get("tags", {})
            ct_str = tags.get("creation_time") or tags.get("CREATION_TIME")
            if ct_str:
                clean_str = ct_str.rstrip("Z").split(".")[0]
                return datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%S").replace(
                    tzinfo=pytz.utc
                )
    except Exception:
        pass
    return None


def get_video_duration(file_path: Path) -> float:
    if file_path.suffix.lower() not in [".mp4", ".mov"]:
        return 0.0
    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(file_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            dur_str = json.loads(result.stdout).get("format", {}).get("duration", "0")
            return float(dur_str)
    except Exception:
        pass
    return 0.0


def get_chat_media_info(file_path: Path) -> tuple:
    date_str = file_path.name.split("_")[0]
    mid = ""
    parts = file_path.name.split("_", 1)
    if len(parts) > 1:
        mid = parts[1].rsplit(".", 1)[0]

    internal_ct = get_video_creation_time(file_path)
    file_ts = internal_ct.timestamp() if internal_ct else 0.0

    if file_ts == 0.0:
        try:
            stat = file_path.stat()
            for ts in (stat.st_ctime, stat.st_mtime):
                if ts > 0:
                    dt_local = datetime.fromtimestamp(ts)
                    time_part = dt_local.strftime("%H:%M:%S")
                    file_utc_dt = datetime.strptime(
                        f"{date_str} {time_part}", "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=pytz.utc)
                    file_ts = file_utc_dt.timestamp()
                    break
        except Exception:
            pass

    matched_entry = None

    # Priority 1: Match mid in chat_metadata_map
    if mid and mid in chat_metadata_map:
        matched_entry = chat_metadata_map[mid]

    # Helper function to find best matching candidate close in timestamp
    def find_best_candidate(candidates_list):
        if not candidates_list:
            return None
        # If all candidates have identical From and Title, pick first
        senders = {c.get("From") for c in candidates_list if c.get("From")}
        titles = {c.get("Title") for c in candidates_list if c.get("Title")}
        if len(senders) <= 1 and len(titles) <= 1:
            return candidates_list[0]

        # Otherwise find candidate closest in timestamp to file_ts
        best = None
        best_diff = float("inf")
        for c in candidates_list:
            utc_dt = parse_utc_timestamp(c.get("Created", ""))
            if utc_dt:
                c_ts = utc_dt.timestamp()
                diff = abs(c_ts - file_ts) if file_ts > 0 else 0
                if diff < best_diff:
                    best_diff = diff
                    best = c
        # Only accept if diff is reasonable (<= 4 hours)
        if best and (file_ts == 0 or best_diff <= 14400):
            return best
        return None

    is_video = file_path.suffix.lower() == ".mp4"

    # Priority 2: Match in snap_metadata_list by date_str
    if not matched_entry and snap_metadata_list:
        if is_video:
            candidates = [
                s
                for s in snap_metadata_list
                if s.get("Created", "").startswith(date_str)
                and s.get("Media Type") in ["VIDEO", "MEDIA", None]
            ]
        else:
            candidates = [
                s
                for s in snap_metadata_list
                if s.get("Created", "").startswith(date_str)
                and s.get("Media Type") in ["IMAGE", "MEDIA", None]
            ]
        matched_entry = find_best_candidate(candidates)

    # Priority 3: Match in chat_history_list by date_str
    if not matched_entry and chat_history_list:
        if is_video:
            candidates = [
                c
                for c in chat_history_list
                if c.get("Created", "").startswith(date_str)
                and c.get("Media Type") in ["VIDEO", "MEDIA", None]
            ]
        else:
            candidates = [
                c
                for c in chat_history_list
                if c.get("Created", "").startswith(date_str)
                and c.get("Media Type") in ["IMAGE", "MEDIA", "NOTE", None]
            ]
        matched_entry = find_best_candidate(candidates)

    extra_info = ""
    tags = ["Snapchat"]
    dt_obj = None
    sort_key_ts = file_ts * 1000000.0

    if matched_entry:
        utc_str = matched_entry.get("Created")
        if utc_str:
            try:
                dt_utc = parse_utc_timestamp(utc_str)
                if dt_utc:
                    dt_obj = dt_utc
                    formatted = dt_utc.astimezone(
                        pytz.timezone(system_timezone)
                    ).strftime("%Y:%m:%d %H:%M:%S")

                    if internal_ct:
                        sort_key_ts = internal_ct.timestamp() * 1000000.0
                    elif matched_entry.get("Microseconds"):
                        sort_key_ts = float(matched_entry["Microseconds"])
                    else:
                        sort_key_ts = dt_utc.timestamp() * 1000000.0

                    info_parts = []
                    if matched_entry.get("From"):
                        sender = (
                            "You"
                            if matched_entry.get("IsSender")
                            else matched_entry["From"]
                        )
                        info_parts.append(f"From: {sender}")
                    if matched_entry.get("Title"):
                        info_parts.append(f"Chat: {matched_entry['Title']}")
                    if info_parts:
                        extra_info = " (" + ", ".join(info_parts) + ")"

                    is_sender = matched_entry.get("IsSender")
                    if is_sender is not None:
                        tags.append("Sent" if is_sender else "Received")

                    chat_title = matched_entry.get("Title") or matched_entry.get(
                        "From"
                    )
                    if chat_title:
                        tags.append(chat_title)

                    return formatted, extra_info, tags, dt_obj, sort_key_ts
            except Exception:
                pass

    # Priority 4: Fall back to file stat timestamp
    try:
        stat = file_path.stat()
        for ts in (stat.st_ctime, stat.st_mtime):
            if ts > 0:
                dt = datetime.fromtimestamp(ts)
                dt_obj = pytz.timezone(system_timezone).localize(dt)
                time_part = dt.strftime("%H:%M:%S")
                formatted = datetime.strptime(
                    f"{date_str} {time_part}", "%Y-%m-%d %H:%M:%S"
                ).strftime("%Y:%m:%d %H:%M:%S")
                sort_key_ts = ts * 1000000.0
                return formatted, extra_info, tags, dt_obj, sort_key_ts
    except Exception:
        pass

    # Priority 5: Fall back to 00:00:00
    dt_fallback = datetime.strptime(f"{date_str} 00:00:00", "%Y-%m-%d %H:%M:%S")
    dt_obj = pytz.timezone(system_timezone).localize(dt_fallback)
    formatted = dt_fallback.strftime("%Y:%m:%d %H:%M:%S")
    return formatted, extra_info, tags, dt_obj, sort_key_ts


def process_chat_media():
    input_dir = Path("input/chat_media")
    output_dir = Path("output/chat media")
    voice_dir = Path("output/chat media voice messages")
    output_dir.mkdir(parents=True, exist_ok=True)
    voice_dir.mkdir(parents=True, exist_ok=True)
    date_counter = {}
    voice_counter = {}

    images = []
    videos = []
    voice_messages = []

    image_exts = [".jpg", ".jpeg", ".png", ".gif", ".webp"]
    video_exts = [".mp4", ".mov"]

    for file in sorted(input_dir.iterdir()):
        # Skip unsupported file types
        if file.suffix.lower() not in image_exts + video_exts:
            print(f"\n→  Skipping unsupported file type → {file.name}")
            continue

        # Skip thumbnails
        if "thumbnail" in file.name.lower():
            print(f"\n→  Skipping thumbnail file → {file.name}")
            continue

        if file.suffix.lower() in image_exts:
            images.append(file)
        elif file.suffix.lower() in video_exts:
            has_video = has_video_stream(file)
            has_audio = has_audio_stream(file)

            if not has_video and not has_audio:
                print(f"\n→  Skipping invalid video (no audio/video) → {file.name}")
                continue

            if has_video:
                videos.append(file)
            elif has_audio:
                voice_messages.append(file)

    # Process images
    for file in images:
        date_str = file.name.split("_")[0]
        formatted, extra_info, tags, dt_obj, sort_key_ts = get_chat_media_info(file)

        date_counter.setdefault(date_str, 0)
        date_counter[date_str] += 1
        suffix = date_counter[date_str]

        new_name = f"{date_str}_chat_media_{suffix}{file.suffix.lower()}"
        new_file = output_dir / new_name
        shutil.copy2(file, new_file)
        update_metadata(new_file, formatted, tags=tags)
        print(f"\n→  Processing chat_media: {file.name}{extra_info}")
        print(f"   Final datetime → {formatted}")
        print(f"   Tags → {tags}")
        print(f"   File name updated → {new_name}")
        print(f"   Added to → chat media")

    # Process voice messages
    for file in voice_messages:
        date_str = file.name.split("_")[0]
        formatted, extra_info, tags, dt_obj, sort_key_ts = get_chat_media_info(file)

        voice_counter.setdefault(date_str, 0)
        voice_counter[date_str] += 1
        suffix = voice_counter[date_str]

        new_name = f"{date_str}_voice_message_{suffix}.mp3"
        new_file = voice_dir / new_name
        success = convert_to_mp3(file, new_file)
        if success:
            update_metadata(new_file, formatted, tags=tags)
            print(f"\n→  Converted voice message to mp3 → {file.name}{extra_info}")
            print(f"   Final datetime → {formatted}")
            print(f"   Tags → {tags}")
            print(f"   File name updated → {new_name}")
            print(f"   Added to → chat media voice messages")
        else:
            print(f"→  Failed to convert voice message → {file.name}")

    # Process & Merge videos
    video_infos = []
    for file in videos:
        formatted, extra_info, tags, dt_obj, sort_key_ts = get_chat_media_info(file)
        ct = get_video_creation_time(file)
        dur = get_video_duration(file)
        start_dt = ct if ct else dt_obj
        video_infos.append(
            {
                "file": file,
                "formatted": formatted,
                "extra_info": extra_info,
                "tags": tags,
                "dt_obj": dt_obj,
                "start_dt": start_dt,
                "dur": dur,
                "sort_key_ts": sort_key_ts,
                "date_str": file.name.split("_")[0],
            }
        )

    # Sort key: (date_str, start_dt, -dur, st_mtime_ns, filename)
    video_infos.sort(
        key=lambda x: (
            x["date_str"],
            x["start_dt"] if x["start_dt"] else datetime.min,
            -x["dur"],
            x["file"].stat().st_mtime_ns if x["file"].exists() else 0,
            x["file"].name,
        )
    )

    groups = []
    current_group = []

    for v_info in video_infos:
        if not current_group:
            current_group.append(v_info)
            continue

        prev_info = current_group[-1]
        same_date = prev_info["date_str"] == v_info["date_str"]
        same_tags = prev_info["tags"] == v_info["tags"]

        if prev_info["start_dt"] and v_info["start_dt"]:
            time_diff = abs(
                (v_info["start_dt"] - prev_info["start_dt"]).total_seconds()
            )
            gap = abs(time_diff - prev_info["dur"])
            is_strict_continuous = (
                time_diff <= 2.0
                or gap <= 2.5
                or (time_diff <= prev_info["dur"] + 2.5 and time_diff >= prev_info["dur"] - 2.5)
            )
        else:
            is_strict_continuous = False

        if same_date and same_tags and is_strict_continuous:
            current_group.append(v_info)
        else:
            groups.append(current_group)
            current_group = [v_info]

    if current_group:
        groups.append(current_group)

    for group in groups:
        first_v = group[0]
        date_str = first_v["date_str"]
        date_counter.setdefault(date_str, 0)
        date_counter[date_str] += 1
        suffix = date_counter[date_str]
        new_name = f"{date_str}_chat_media_{suffix}.mp4"
        output_file = output_dir / new_name

        if len(group) > 1:
            print(
                f"\n→  Merging chat media videos: {[v['file'].name for v in group]}{first_v['extra_info']}"
            )
            clips_to_merge = [v["file"] for v in group]
            success = concat_video_files(clips_to_merge, output_file)
            if success:
                update_metadata(output_file, first_v["formatted"], tags=first_v["tags"])
                print(f"   Final datetime → {first_v['formatted']}")
                print(f"   Tags → {first_v['tags']}")
                print(f"   Merged video saved → {new_name}")
                print(f"   Added to → chat media")
            else:
                print(f"   [Warning] Video merge failed. Copying clips individually...")
                date_counter[date_str] -= 1
                for v in group:
                    date_counter[date_str] += 1
                    indiv_suffix = date_counter[date_str]
                    indiv_name = f"{date_str}_chat_media_{indiv_suffix}.mp4"
                    indiv_out = output_dir / indiv_name
                    shutil.copy2(v["file"], indiv_out)
                    update_metadata(indiv_out, v["formatted"], tags=v["tags"])
                    print(f"   Fallback saved individual clip → {indiv_name}")
        else:
            shutil.copy2(first_v["file"], output_file)
            update_metadata(output_file, first_v["formatted"], tags=first_v["tags"])
            print(
                f"\n→  Processing chat_media: {first_v['file'].name}{first_v['extra_info']}"
            )
            print(f"   Final datetime → {first_v['formatted']}")
            print(f"   Tags → {first_v['tags']}")
            print(f"   File name updated → {new_name}")
            print(f"   Added to → chat media")


def main():
    try:
        process_chat_media()
        process_memories()
    finally:
        _exiftool_runner.close()


if __name__ == "__main__":
    main()
