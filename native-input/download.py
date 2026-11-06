import json
from pathlib import Path
import subprocess
import urllib.request
import urllib.error
import sys
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args): return None
artifact, directory = sys.argv[1:]
dest = Path(directory)
dest.mkdir(parents=True, exist_ok=False)
token = subprocess.check_output(['gh', 'auth', 'token']).decode().strip()
req = urllib.request.Request('https://api.github.com/repos/collegeming/panvk-mali-nctx-ci/actions/artifacts/' + artifact + '/zip', headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
try:
    urllib.request.build_opener(NoRedirect).open(req)
    raise RuntimeError('Expected redirect')
except urllib.error.HTTPError as e:
    if e.code != 302: raise
    url = e.headers['Location']
subprocess.run(['E:/SoftWare/Aria2/aria2c.exe', '--no-conf', '-x16', '-s16', '-k1M', '--dir=' + str(dest), '--out=artifact.zip', '--summary-interval=0', url], check=True, stdout=subprocess.DEVNULL)
import zipfile
with zipfile.ZipFile(dest / 'artifact.zip') as z:
    if z.testzip(): raise RuntimeError('ZIP CRC failed')
    z.extractall(dest / 'extracted')
print('Downloaded and CRC checked:', dest)
