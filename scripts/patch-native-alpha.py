from pathlib import Path
import sys

p = Path(sys.argv[1]) / 'gpu_display/src/lib.rs'
s = p.read_text()
def rep(old, new):
    global s
    assert s.count(old) == 1, old[:100]
    s = s.replace(old, new, 1)
rep('    for pixel in 0..width {\n        let source', '''    let source_opaque = matches!(source_fourcc, DRM_FORMAT_XRGB8888 | DRM_FORMAT_XBGR8888);
    for pixel in 0..width {
        let source''')
rep('        let target = &mut target[pixel * 4..];', '''        let a = if source_opaque { 0xff } else { a };
        let target = &mut target[pixel * 4..];''')
rep('''    /// X-versus-alpha does not require a conversion: both variants put their fourth byte in the
    /// same place. Only the red/blue order matters for the 32-bit CPU fast path.''', '''    /// X has no alpha semantics. An XRGB source copied into an alpha-bearing sink must
    /// explicitly become opaque rather than trusting the undefined fourth byte.''')
rep('''        self.bytes_per_pixel == 4
            && matches!(''', '''        self.bytes_per_pixel == 4
            && !(matches!(source_fourcc, DRM_FORMAT_XRGB8888 | DRM_FORMAT_XBGR8888)
                && matches!(self.fourcc, DRM_FORMAT_ARGB8888 | DRM_FORMAT_ABGR8888))
            && matches!(''')
tests = '''
#[cfg(test)]
mod opaque_tests {
    use super::*;
    #[test]
    fn xrgb_is_opaque_when_converted_to_android_rgba() {
        let mut target = [0; 8];
        assert!(convert_scanout_row(DRM_FORMAT_XRGB8888, DRM_FORMAT_ABGR8888,
            2, &[3, 2, 1, 0, 6, 5, 4, 7], &mut target));
        assert_eq!(target, [1, 2, 3, 255, 4, 5, 6, 255]);
    }
    #[test]
    fn same_order_xbgr_is_opaque_in_abgr() {
        let mut target = [0; 4];
        assert!(convert_scanout_row(DRM_FORMAT_XBGR8888, DRM_FORMAT_ABGR8888,
            1, &[1, 2, 3, 0], &mut target));
        assert_eq!(target, [1, 2, 3, 255]);
    }
    #[test]
    fn argb_preserves_real_alpha() {
        let mut target = [0; 4];
        assert!(convert_scanout_row(DRM_FORMAT_ARGB8888, DRM_FORMAT_ABGR8888,
            1, &[3, 2, 1, 19], &mut target));
        assert_eq!(target, [1, 2, 3, 19]);
    }
    #[test]
    fn short_source_is_rejected() {
        let mut target = [0; 4];
        assert!(!convert_scanout_row(DRM_FORMAT_XRGB8888, DRM_FORMAT_ABGR8888,
            1, &[3, 2, 1], &mut target));
    }
}
'''
s += tests
p.write_text(s)
# Test the actual conversion function, not a duplicated model, without Android linkage.
start = s.index('pub const fn drm_fourcc(')
end = s.index('#[derive(Clone)]\npub struct VulkanCreateParams', start)
Path(sys.argv[2]).write_text(s[start:end] + tests)
