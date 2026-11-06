"""Assemble isolated sources; executed by Actions, never from downloaded trees."""
from pathlib import Path
import sys
import shutil

source, target = map(Path, sys.argv[1:])
java = source / 'app/src/main/java/cn/classfun/droidvm'
out = target / 'src/io/github/collegeming/panvk'
out.mkdir(parents=True, exist_ok=True)
files = [
    'ui/vm/display/nativedisplay/input/EvdevEncoder.java',
    'ui/vm/display/nativedisplay/input/KeyCodeMapper.java',
    'ui/vm/display/nativedisplay/input/InputForwarder.java',
    'ui/vm/display/nativedisplay/display/DisplayProvider.java',
    'ui/vm/display/nativedisplay/display/CursorPositionStream.java',
    'ui/vm/display/nativedisplay/display/NativeSurfaceSource.java',
    'ui/vm/display/base/DisplaySource.java',
]
for name in files:
    text = (java / name).read_text(encoding='utf-8')
    for package in ['ui.vm.display.nativedisplay.input', 'ui.vm.display.nativedisplay.display', 'ui.vm.display.base']:
        text = text.replace('cn.classfun.droidvm.' + package, 'io.github.collegeming.panvk')
    text = text.replace('cn.classfun.droidvm.lib.store.vm.NativeDisplay', 'io.github.collegeming.panvk.Channels')
    text = text.replace('cn.classfun.droidvm.lib.utils.StringUtils', 'io.github.collegeming.panvk.Strings')
    text = '\n'.join(line for line in text.splitlines() if not line.startswith('import androidx.annotation.'))
    for annotation in ['@NonNull ', '@Nullable ', '@NonNull', '@Nullable']:
        text = text.replace(annotation, '')
    (out / Path(name).name).write_text(text + '\n', encoding='utf-8')
for name in ['DisplayConfig.aidl', 'ICrosvmAndroidDisplayService.aidl']:
    dest = target / 'src/android/crosvm' / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / 'app/src/main/aidl/android/crosvm' / name, dest)
for name in ['LICENSE.txt', 'ADDITIONAL-PERMISSIONS']:
    shutil.copyfile(source / name, target / name)
(out / 'Channels.java').write_text('package io.github.collegeming.panvk; final class Channels { static final int MULTITOUCH=0,KEYBOARD=1,MOUSE=2,TABLET=3; }\n')
(out / 'InputMode.java').write_text('package io.github.collegeming.panvk; public enum InputMode { TOUCH, MOUSE, TABLET }\n')
(out / 'Strings.java').write_text('package io.github.collegeming.panvk; final class Strings { static String fmt(String s,Object... a) { return String.format(java.util.Locale.ROOT,s,a); } }\n')
