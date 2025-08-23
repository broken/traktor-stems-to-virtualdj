import os
import re
import shutil
import subprocess
import tempfile
import zlib

# Update these values as needed
TRAKTOR_STEMS_DIR = "/Volumes/T7/drive/stems"
VDJ_STEMS_DIR = "/Volumes/T7/music/vdj_stems"
TRAKTOR_SUFFIX = ".stem.m4a"
# TODO this assumes all original files are mp3s. Other extensions would have
# other vdj suffixes. (Example: mp4 -> mp4.vdjstems). Worth fixing?
VDJ_SUFFIX = ".mp3.vdjstems"
# Directory where "non-stem'd" mp3 files are kept. No ending slash.
BASE_MP3_DIR_FOR_CHECKSUM = '/Users/dogatech/Drive/music/mp3'
# Use this if you only want to convert a subset of the stems.
REGEX_FILTER=r''

def get_dir_checksum_and_suffix(directory_path):
  # Remove separaters before getting the last 4 characters.
  dir_name = directory_path.replace(os.path.sep, "")
  last_4 = dir_name[max(0, len(dir_name) - 4):]

  # Compute the CRC32 checksum as a 32-bit integer, then format as hex string.
  csum = zlib.crc32(directory_path.encode('utf-8')) & 0xffffffff
  chksum = f'{csum:08x}'

  return last_4, chksum

def organize_files():
  """
  Recursively finds and moves files with a specific suffix to a new location,
  renaming them based on their original directory's checksum.
  """
  if not os.path.exists(VDJ_STEMS_DIR):
    print(f"Creating output directory: {VDJ_STEMS_DIR}")
    os.makedirs(VDJ_STEMS_DIR)

  print(f"Starting file transformation from '{TRAKTOR_STEMS_DIR}' to '{VDJ_STEMS_DIR}'...")

  # Metadata file to be used in VDJ file
  with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as metadata_file:
    metadata_file.write("tool=VirtualDJ 2023.7544\n")
    metadata_file.write("created=0\n")
    metadata_file.write("rate=0\n")
    metadata_file.flush()  # Ensure the content is written to the file


  # os.walk() yields a 3-tuple: (dirpath, dirnames, filenames).
  for dirpath, _, filenames in os.walk(TRAKTOR_STEMS_DIR):
    for filename in filenames:
      if not filename.endswith(TRAKTOR_SUFFIX):
        continue

      source_path = os.path.join(dirpath, filename)
      if not re.search(REGEX_FILTER, source_path):
        continue

      if (dirpath == TRAKTOR_STEMS_DIR):
        path_for_checksum = BASE_MP3_DIR_FOR_CHECKSUM
      else:
        relative_path = os.path.relpath(dirpath, TRAKTOR_STEMS_DIR)
        path_for_checksum = os.path.join(BASE_MP3_DIR_FOR_CHECKSUM, relative_path)

      # Construct output subdirectory.
      last_4, chksum = get_dir_checksum_and_suffix(path_for_checksum)
      subdir = f"User..{last_4}-{chksum}"

      # Construct new filename.
      new_filename = os.path.basename(filename).removesuffix(TRAKTOR_SUFFIX) + VDJ_SUFFIX

      # Construct the full destination path.
      destination_dir = os.path.join(VDJ_STEMS_DIR, subdir)
      destination_path = os.path.join(destination_dir, new_filename)

      # Check if the destination file already exists.
      if os.path.exists(destination_path):
        print(f"File already exists, ignoring: {destination_path}")
        continue

      # Create directory if it doesn't already exist
      if not os.path.exists(destination_dir):
        print(f"Creating output directory: {destination_dir}")
        os.makedirs(destination_dir)

      try:
        print(f"Converting {source_path}...")
        # Use temporary directory for intermediate files.
        temp_dir = destination_dir
        base_name = os.path.basename(filename).removesuffix(TRAKTOR_SUFFIX)

        # Determine song duration.
        duration_cmd = [
            'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
            '-of', 'csv=p=0', source_path
        ]
        duration = subprocess.check_output(duration_cmd).decode('utf-8').strip()

        # Create silent audio track for hihats.
        silent_path = os.path.join(temp_dir, 'silent.m4a')
        silent_cmd = [
            'ffmpeg', '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-t', duration, '-c:a', 'aac', silent_path
        ]
        subprocess.run(silent_cmd, check=True, capture_output=True)

        # Extract stem tracks.
        extracted_stems_paths = []
        extract_cmd = ['ffmpeg', '-i', source_path]
        # Traktor stems typically have a mixed track followed by the stems:
        # drums, bass, inst, vocal
        for i in range(5):
            stem_path = os.path.join(temp_dir, f'stem_{i}.m4a')
            extract_cmd.extend(['-map', f'0:a:{i}', '-c', 'copy', stem_path])
            extracted_stems_paths.append(stem_path)
        subprocess.run(extract_cmd, check=True, capture_output=True)

        # Re-muxing into a new M4A file with correct track order.
        temp_output_path = os.path.join(temp_dir, f'{base_name}.tmp.m4a')
        remux_cmd = ['ffmpeg']
        remux_cmd.extend([
            '-i', extracted_stems_paths[0], # Mixed Track
            '-i', extracted_stems_paths[4], # Vocal
            '-i', silent_path,              # HiHat
            '-i', extracted_stems_paths[2], # Bass
            '-i', extracted_stems_paths[3], # Instruments
            '-i', extracted_stems_paths[1], # Kick
        ])
        for i in range(6):
          remux_cmd.extend(['-map', f'{i}:a'])
        remux_cmd.extend(['-c:a', 'copy'])
        for i in range(6):
          remux_cmd.extend([f'-disposition:a:{i}', '0'])
        remux_cmd.extend([
            '-brand', 'isom',
            '-metadata:s:a:0', 'title="mixed track"',
            '-metadata:s:a:1', 'title="vocal"',
            '-metadata:s:a:2', 'title="hihat"',
            '-metadata:s:a:3', 'title="bass"',
            '-metadata:s:a:4', 'title="instruments"',
            '-metadata:s:a:5', 'title="kick"',
            temp_output_path
        ])
        subprocess.run(remux_cmd, check=True, capture_output=True)

        # Tag the tracks properly using MP4Box.
        final_output_path = os.path.join(temp_dir, f'{base_name}.mp3.m4a')
        mp4box_cmd = ['MP4Box']
        tags = ['mixed track', 'vocal', 'hihat', 'bass', 'instruments', 'kick']
        for i, tag in enumerate(tags):
          mp4box_cmd.extend(['-udta', f'{i+1}:type=name', '-udta', f'{i+1}:type=name:str="{tag}"'])
        mp4box_cmd.extend([
            '-itags', metadata_file.name,
            '-flat', '-brand', 'isom:512',
            '-rb', 'mp42', '-ab', 'mp41',
            '-out', final_output_path,
            temp_output_path
        ])
        subprocess.run(mp4box_cmd, check=True, capture_output=True)

        # Finally, rename the file and clean up.
        shutil.move(final_output_path, destination_path)
        print(f"Successfully converted and copied to {destination_path}")

        # Clean up temporary files.
        for path in [silent_path, temp_output_path] + extracted_stems_paths:
            if os.path.exists(path):
                os.remove(path)

      except subprocess.CalledProcessError as e:
        print(f"Error converting file {source_path}: {e.stderr.decode()}")
      except Exception as e:
        print(f"Error processing file {source_path}: {e}")

  print("File organization complete.")

if __name__ == "__main__":
  organize_files()
