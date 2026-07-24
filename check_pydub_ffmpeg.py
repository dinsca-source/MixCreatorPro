import os
from pydub import AudioSegment
import pydub.utils as utils
import inspect

print('pydub module:', AudioSegment.__module__)
print('AudioSegment converter:', getattr(AudioSegment, 'converter', None))
print('AudioSegment ffmpeg:', getattr(AudioSegment, 'ffmpeg', None))
print('AudioSegment ffprobe:', getattr(AudioSegment, 'ffprobe', None))
print('AudioSegment prober:', getattr(AudioSegment, 'prober', None))
print('utils FFMPEG_BINARY:', getattr(utils, 'FFMPEG_BINARY', None))
print('utils FFPROBE_BINARY:', getattr(utils, 'FFPROBE_BINARY', None))
print('utils.which ffmpeg:', utils.which('ffmpeg'))
print('utils.which ffprobe:', utils.which('ffprobe'))
print('utils.which avprobe:', utils.which('avprobe'))
print('utils.which ffmpeg.exe:', utils.which('ffmpeg.exe'))
print('utils.which ffprobe.exe:', utils.which('ffprobe.exe'))
print('utils.which avprobe.exe:', utils.which('avprobe.exe'))
print('utils.which path if any:', os.environ.get('PATH'))
