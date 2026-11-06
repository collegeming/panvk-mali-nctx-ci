"""Generate CI-only display profiling variant from verified native authoring source."""
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text(encoding='utf-8')
s = s.replace('name: build-crosvm-panvk', 'name: PanVK native display profile', 1)
needle = '                          if native_blob {\n                              static PROBES:'
assert s.count(needle) == 1
s = s.replace(needle, '''                          if native_blob && std::env::var_os("PANVK_PRESENT_PROFILE").is_some() {
                              static FRAMES: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
                              let frame_n = FRAMES.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                              if frame_n < 20 || frame_n % 120 == 0 {
                                  base::info!("PANVK-PRESENT-TIME: frame={} res={} us={} bytes={} flipped={}",
                                      frame_n, resource.resource_id, present_start.elapsed().as_micros(),
                                      frame.bytes.len(), matches!(outcome, PresentOutcome::Flipped));
                              }
                          }
                          if native_blob {
                              static PROBES:''', 1)
needle = '          new = \'\'\'                let outcome = display.present_frame(surface_id, &frame);'
assert s.count(needle) == 1
s = s.replace(needle, '''          new = ''' + "'''" + '''                let present_start = std::time::Instant::now();
                          let outcome = display.present_frame(surface_id, &frame);''', 1)
Path(sys.argv[2]).write_text(s, encoding='utf-8')
