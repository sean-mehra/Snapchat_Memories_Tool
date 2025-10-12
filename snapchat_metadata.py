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


# Load metadata
with open("input/memories_history.json", "r", encoding="utf-8") as f:
    metadata = json.load(f)["Saved Media"]

tf = TimezoneFinder()
system_timezone = get_localzone_name()


def get_metadata(filename):
    for m in metadata:
        if "mid=" in m["Download Link"]:
            mid = m["Download Link"].split("mid=")[1].split("&")[0]
            if mid in filename:
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


def update_metadata(file_path, date_time, gps_coords=None, only_modified=False):
    current_time = datetime.now().strftime("%Y:%m:%d %H:%M:%S")
    if not only_modified:
        clean_cmd = [
            "exiftool",
            "-overwrite_original",
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
            str(file_path),
        ]
        subprocess.run(clean_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    set_cmd = [
        "exiftool",
        "-overwrite_original",
        f"-FileCreateDate={current_time}",
        f"-FileModifyDate={current_time}",
    ]

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

    set_cmd.append(str(file_path))
    subprocess.run(set_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        timestamp = datetime.strptime(date_time, "%Y:%m:%d %H:%M:%S").timestamp()
        os.utime(file_path, (timestamp, timestamp))
    except:
        pass


def generate_filename(date_time_str, has_caption=False):
    dt_obj = datetime.strptime(date_time_str, "%Y:%m:%d %H:%M:%S")
    base = dt_obj.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{base}_with_caption" if has_caption else base


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
                "-y",
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
            "-i", str(overlay_path),
            "-vf", f"scale={width}:{height}",
            "-y",
            str(resized_overlay)
        ]
        subprocess.run(resize_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Apply overlay directly
        overlay_cmd = [
            "ffmpeg",
            "-i", str(base_path),
            "-i", str(resized_overlay),
            "-filter_complex", "overlay=0:0",
            "-c:a", "copy",
            "-movflags", "+faststart",
            "-y",
            str(output_path)
        ]
        subprocess.run(overlay_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if resized_overlay.exists():
            os.remove(resized_overlay)

        return output_path.exists()

    except Exception as e:
        print(f"   Overlay video failed (portrait) for {base_path.name}: {e}")
        return False


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

        concat_list = Path("temp_inputs.txt")
        with open(concat_list, "w") as f:
            for clip in group:
                f.write(f"file '{clip.as_posix()}'\n")

        print(f"\n→  Merging videos (location, date, and time match): {[clip.name for clip in group]}")

        if gps_coords and gps_coords != "0.0, 0.0":
            print(f"   Location → ({gps_coords})")
            if gps_tz:
                print(f"   Timezone used → {gps_tz}")
        else:
            print("   Location → none found")
            print(f"   System timezone used → {system_timezone}")
        print(f"   Final datetime → {gps_local_str}")

        subprocess.run(
            ["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c", "copy", str(merged_path_location)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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

        subprocess.run(
            ["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c", "copy", str(merged_path_system)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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
            "ffmpeg", "-i", str(input_file), "-vn", "-acodec", "libmp3lame", str(output_file)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_file.exists()
    except Exception:
        return False


def process_chat_media():
    input_dir = Path("input/chat_media")
    output_dir = Path("output/chat media")
    voice_dir = Path("output/chat media voice messages")
    output_dir.mkdir(parents=True, exist_ok=True)
    voice_dir.mkdir(parents=True, exist_ok=True)
    date_counter = {}
    voice_counter = {}

    for file in sorted(input_dir.iterdir()):
        # Skip unsupported file types
        if file.suffix.lower() not in [".jpg", ".jpeg", ".mp4"]:
            print(f"\n→  Skipping unsupported file type → {file.name}")
            continue

        # Skip thumbnails
        if "thumbnail" in file.name.lower():
            print(f"\n→  Skipping thumbnail file → {file.name}")
            continue

        date_str = file.name.split("_")[0]

        # Process image files
        if file.suffix.lower() in [".jpg", ".jpeg"]:
            date_counter.setdefault(date_str, 0)
            date_counter[date_str] += 1
            suffix = date_counter[date_str]

            new_name = f"{date_str}_chat_media_{suffix}{file.suffix.lower()}"
            new_file = output_dir / new_name
            shutil.copy2(file, new_file)
            formatted = datetime.strptime(f"{date_str} 00:00:00", "%Y-%m-%d %H:%M:%S").strftime("%Y:%m:%d %H:%M:%S")
            update_metadata(new_file, formatted)
            print(f"\n→  Processing chat_media: {file.name}")
            print(f"   Final datetime → {formatted}")
            print(f"   File name updated → {new_name}")
            print(f"   Added to → chat media")

        # Process video/audio files
        elif file.suffix.lower() == ".mp4":
            has_video = has_video_stream(file)
            has_audio = has_audio_stream(file)

            if not has_video and not has_audio:
                print(f"\n→  Skipping invalid mp4 (no audio/video) → {file.name}")
                continue

            formatted = datetime.strptime(f"{date_str} 00:00:00", "%Y-%m-%d %H:%M:%S").strftime("%Y:%m:%d %H:%M:%S")

            if has_video:
                date_counter.setdefault(date_str, 0)
                date_counter[date_str] += 1
                suffix = date_counter[date_str]

                new_name = f"{date_str}_chat_media_{suffix}.mp4"
                new_file = output_dir / new_name
                shutil.copy2(file, new_file)
                update_metadata(new_file, formatted)
                print(f"\n→  Processing chat_media: {file.name}")
                print(f"   Final datetime → {formatted}")
                print(f"   File name updated → {new_name}")
                print(f"   Added to → chat media")

            elif has_audio:
                voice_counter.setdefault(date_str, 0)
                voice_counter[date_str] += 1
                suffix = voice_counter[date_str]

                new_name = f"{date_str}_voice_message_{suffix}.mp3"
                new_file = voice_dir / new_name
                success = convert_to_mp3(file, new_file)
                if success:
                    update_metadata(new_file, formatted)
                    print(f"\n→  Converted voice message to mp3 → {file.name}")
                    print(f"   Final datetime → {formatted}")
                    print(f"   File name updated → {new_name}")
                    print(f"   Added to → chat media voice messages")
                else:
                    print(f"→  Failed to convert voice message → {file.name}")


def main():
    process_chat_media()
    process_memories()


if __name__ == "__main__":
    main()
