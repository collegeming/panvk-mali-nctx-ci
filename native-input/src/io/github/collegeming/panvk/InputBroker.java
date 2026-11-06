package io.github.collegeming.panvk;

import android.content.Context;
import android.content.Intent;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Binder;
import android.os.Bundle;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;
import java.io.OutputStream;
import java.util.HashSet;
import java.util.Set;

/** Root-only bridge for the isolated PanVK test instance, not DroidVM's daemon. */
public final class InputBroker {
    static final String PACKAGE = "io.github.collegeming.panvk";
    static final String BASE = "/data/local/tmp/panvk-stability-v1/input";
    static final String DISPLAY = "droidvm_disp_panvk_stability_native_gpu-0";
    private static final LocalSocket[] peers = new LocalSocket[4];
    private static final LocalSocket[] listeners = new LocalSocket[4];
    private static final Set<Integer>[] keys = new Set[]{new HashSet<>(), new HashSet<>(), new HashSet<>(), new HashSet<>()};
    private static long batches;

    private static synchronized boolean send(int channel, byte[] data) {
        if (channel < 0 || channel >= 4 || data == null || data.length == 0
                || data.length > 16384 || data.length % 8 != 0 || peers[channel] == null) return false;
        try {
            OutputStream out = peers[channel].getOutputStream();
            out.write(data);
            var b = java.nio.ByteBuffer.wrap(data).order(java.nio.ByteOrder.LITTLE_ENDIAN);
            while (b.remaining() >= 8) {
                int type = b.getShort() & 65535, code = b.getShort() & 65535, value = b.getInt();
                if (type == 1) {
                    if (value == 0) keys[channel].remove(code); else keys[channel].add(code);
                }
            }
            batches++;
            if (batches <= 16 || batches % 100 == 0)
                Log.i("PanVKInput", "channel=" + channel + " records=" + data.length / 8 + " batches=" + batches);
            return true;
        } catch (Exception e) { Log.e("PanVKInput", "write", e); return false; }
    }

    private static synchronized void release() {
        for (int ch = 0; ch < 4; ch++) {
            for (int code : new HashSet<>(keys[ch])) send(ch, record(1, code, 0));
            if (ch == 0) {
                for (int slot = 0; slot < 16; slot++) {
                    send(ch, record(3, 0x2f, slot));
                    send(ch, record(3, 0x39, -1));
                }
            }
            send(ch, record(0, 0, 0));
        }
    }

    private static byte[] record(int type, int code, int value) {
        return java.nio.ByteBuffer.allocate(8).order(java.nio.ByteOrder.LITTLE_ENDIAN)
            .putShort((short)type).putShort((short)code).putInt(value).array();
    }

    public static void main(String[] args) throws Exception {
        if (android.os.Process.myUid() != 0 || args.length != 2 || !args[0].matches("[0-9a-f]{64}"))
            throw new SecurityException("root and session token required");
        String token = args[0];
        int uid = Integer.parseInt(args[1]);
        Looper.prepareMainLooper();
        Context context = (Context) Class.forName("android.app.ActivityThread")
            .getMethod("getSystemContext").invoke(Class.forName("android.app.ActivityThread")
            .getMethod("systemMain").invoke(null));
        if (context.getPackageManager().getApplicationInfo(PACKAGE, 0).uid != uid)
            throw new SecurityException("package UID mismatch");
        for (int i = 0; i < 4; i++) {
            final int ch = i;
            java.io.File stale = new java.io.File(BASE + ch + ".sock");
            if (stale.exists() && !stale.delete()) throw new java.io.IOException("stale socket: " + stale);
        }
        // LocalServerSocket(String) uses the abstract namespace; file-backed sockets use bind + fd.
        for (int i = 0; i < 4; i++) {
            final int ch = i;
            LocalSocket listener = new LocalSocket();
            listener.bind(new android.net.LocalSocketAddress(BASE + ch + ".sock", android.net.LocalSocketAddress.Namespace.FILESYSTEM));
            listeners[ch] = listener;
            LocalServerSocket server = new LocalServerSocket(listener.getFileDescriptor());
            new Thread(() -> {
                while (true) try {
                    LocalSocket peer = server.accept();
                    synchronized (InputBroker.class) {
                        if (peers[ch] != null) peers[ch].close();
                        peers[ch] = peer;
                        keys[ch].clear();
                    }
                    Log.i("PanVKInput", "connected channel=" + ch);
                    byte[] status = new byte[256];
                    while (peer.getInputStream().read(status) >= 0) { }
                    synchronized (InputBroker.class) { if (peers[ch] == peer) peers[ch] = null; }
                    peer.close();
                } catch (Exception e) { Log.e("PanVKInput", "accept/status", e); }
            }, "PanVK-input-" + ch).start();
        }
        IBinder display = (IBinder) Class.forName("android.os.ServiceManager")
            .getMethod("checkService", String.class).invoke(null, DISPLAY);
        final IInputBroker.Stub broker = new IInputBroker.Stub() {
            private void check(String supplied) {
                if (Binder.getCallingUid() != uid || !token.equals(supplied)) throw new SecurityException("session denied");
            }
            public IBinder display(String supplied) throws android.os.RemoteException {
                check(supplied);
                try { return (IBinder) Class.forName("android.os.ServiceManager").getMethod("checkService", String.class).invoke(null, DISPLAY); }
                catch (Exception e) { throw new IllegalStateException(e); }
            }
            public boolean write(String supplied, int channel, byte[] bytes) { check(supplied); return send(channel, bytes); }
            public void releaseAll(String supplied) { check(supplied); release(); }
            public void watch(String supplied, IBinder client) throws android.os.RemoteException {
                check(supplied); client.linkToDeath(InputBroker::release, 0);
            }
        };
        Bundle bundle = new Bundle(); bundle.putBinder("binder", broker); bundle.putString("token", token);
        new android.os.Handler().postDelayed(new Runnable() {
            public void run() {
                Intent intent = new Intent(PACKAGE + ".BROKER").setPackage(PACKAGE).putExtra("session", bundle);
                context.sendBroadcast(intent);
                new android.os.Handler().postDelayed(this, 2000);
            }
        }, 1000);
        Log.i("PanVKInput", "ready uid=" + uid);
        Looper.loop();
    }
}
