package io.github.collegeming.panvk;

final class InputPolicy {
    static boolean authorized(int caller, int owner, String expected, String supplied) {
        return caller == owner && expected != null && expected.equals(supplied);
    }
    static boolean validBatch(int channel, byte[] bytes) {
        return channel >= 0 && channel < 4 && bytes != null && bytes.length > 0
            && bytes.length <= 16384 && bytes.length % 8 == 0;
    }
    static int absolute(float position, int extent) {
        if (extent <= 1 || !Float.isFinite(position)) return 0;
        return Math.round(Math.max(0, Math.min(extent - 1, position)) * 32767 / (extent - 1));
    }
}
