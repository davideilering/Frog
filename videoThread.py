##################################################################################################
# Copyright (c) 2012 Brett Dixon
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in 
# the Software without restriction, including without limitation the rights to use,
# copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the 
# Software, and to permit persons to whom the Software is furnished to do so, 
# subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all 
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR 
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS 
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR 
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER 
# IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION 
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
##################################################################################################


import logging
import re
import time
import subprocess
import json
from threading import Thread

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from path import path as Path

TIMEOUT = 1
ROOT = Path(settings.MEDIA_ROOT.replace('\\', '/'))
logger = logging.getLogger('frog')

try:
    FROG_FFMPEG = getattr(settings, 'FROG_FFMPEG')
except AttributeError:
    raise ImproperlyConfigured, 'FROG_FFMPEG is required'

FROG_SCRUB_DURATION = getattr(settings, 'FROG_SCRUB_DURATION', 60)
FROG_FFMPEG_ARGS = getattr(settings, 'FROG_FFMPEG_ARGS', '-vcodec libx264 -b:v 2500k -acodec libvo_aacenc -b:a 56k -ac 2 -y')
FROG_SCRUB_FFMPEG_ARGS = getattr(settings, 'FROG_SCRUB_FFMPEG_ARGS', '-vcodec libx264 -b:v 2500k -x264opts keyint=1:min-keyint=8 -acodec libvo_aacenc -b:a 56k -ac 2 -y')


class VideoThread(Thread):
    def __init__(self, queue, *args, **kwargs):
        super(VideoThread, self).__init__(*args, **kwargs)
        self.queue = queue
        self.daemon = True

    def run(self):
        while True:
            if self.queue.qsize():
                try:
                    isH264 = False
                    ## -- Get the video object to work on
                    item = self.queue.get()
                    ## -- Set the video to processing
                    item.video = 'frog/i/processing.mp4'
                    item.save()
                    ## -- Set the status of the queue item
                    item.queue.setStatus(item.queue.PROCESSING)
                    item.queue.setMessage('Processing video...')
                    
                    infile = "%s%s" % (ROOT, item.source.name)
                    cmd = '%s -v quiet -show_format -show_streams -print_format json "%s"' % (settings.FROG_FFPROBE, infile)
                    sourcepath = ROOT / item.source.name

                    ## -- Get the video information
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
                    infoString = proc.stdout.read()
                    videodata = parseInfo(infoString)
                    isH264 = videodata['video']['codec'].lower().find('h264') != -1 and sourcepath.ext == '.mp4'

                    scrub = float(videodata['duration']) <= FROG_SCRUB_DURATION

                    outfile = sourcepath.parent / ("_%s.mp4" % item.hash)

                    ## -- Further processing is needed if not h264 or needs to be scrubbable
                    if not isH264 or scrub:
                        item.queue.setMessage('Converting to MP4...')
                        
                        cmd = '{exe} -nostdin -i "{infile}" {args} "{outfile}"'.format(
                            exe=FROG_FFMPEG,
                            infile=infile,
                            args=FROG_SCRUB_FFMPEG_ARGS if scrub else FROG_FFMPEG_ARGS,
                            outfile=outfile,
                        )
                        try:
                            subprocess.call(cmd, shell=True)
                        except subprocess.CalledProcessError:
                            logger.error('Failed to convert video: %s' % item.guid)
                            item.queue.setStatus(item.queue.ERROR)
                            continue

                        item.video = outfile.replace('\\', '/').replace(ROOT, '')
                    else:
                        ## -- No further processing
                        item.video = item.source.name


                    ## -- Set the video to the result
                    logger.info('Finished processing video: %s' % item.guid)
                    item.queue.setStatus(item.queue.COMPLETED)
                    item.queue.setMessage('Completed')

                    item.save()
                except Exception, e:
                    logger.error(str(e))

                time.sleep(TIMEOUT)

def parseInfo(jsonString):
    rawJson = json.loads(jsonString)
    data = {}
    data['duration'] = rawJson['format']['duration']
    streams = rawJson['streams']
    for stream in streams:
        try:
            if(stream['codec_type'] == 'video'):
                data['video'] = {'width': stream['width'], 'height': stream['height'], 'codec': stream['codec_name']}
        except: 
            logger.error('error parsing json ' + stream)
    return data