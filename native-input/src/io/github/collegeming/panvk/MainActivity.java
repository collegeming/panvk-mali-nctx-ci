package io.github.collegeming.panvk;

import android.app.Activity;
import android.content.*;
import android.os.*;
import android.view.*;
import android.widget.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private IInputBroker broker;
    private String token;
    private final IBinder lifetime = new Binder();
    private InputForwarder input;
    private NativeSurfaceSource source;
    private SurfaceView surface;
    private TextView status;
    private InputMode mode = InputMode.TOUCH;
    private boolean resumed;
    private int buttons;
    private float lastX, lastY;
    private boolean haveMouse;
    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        public void onReceive(Context c, Intent intent) {
            Bundle session = intent.getBundleExtra("session");
            if (session == null) return;
            IBinder binder = session.getBinder("binder");
            String next = session.getString("token");
            if (binder == null || next == null || !next.matches("[0-9a-f]{64}")) return;
            if (broker != null && broker.asBinder().equals(binder)) return;
            disconnect();
            broker = IInputBroker.Stub.asInterface(binder); token = next;
            try { broker.watch(token, lifetime); } catch (Exception e) { status.setText(e.toString()); return; }
            input = new InputForwarder((channel, data) -> {
                if (!resumed) return false;
                try { return broker.write(token, channel, data); }
                catch (Exception e) { return false; }
            });
            input.setInputMode(mode);
            source = new NativeSurfaceSource(surface, 2272, 1514, () -> {
                try { return broker.display(token); } catch (Exception e) { return null; }
            }, main, new DisplaySource.Callbacks() {
                public void onContentSize(int w, int h) { fitSurface(); }
                public void onStateChanged(DisplaySource.State state) { status.setText("PanVK native · " + state + " · " + mode); }
            });
        }
    };

    public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        LinearLayout root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL);
        LinearLayout bar = new LinearLayout(this);
        status = new TextView(this); status.setText("PanVK native · waiting for isolated broker");
        bar.addView(status, new LinearLayout.LayoutParams(0, 56, 1));
        Button switchMode = new Button(this); switchMode.setText("Touch / Mouse / Tablet");
        switchMode.setOnClickListener(v -> {
            release(); mode = InputMode.values()[(mode.ordinal() + 1) % 3];
            if (input != null) input.setInputMode(mode);
            status.setText("PanVK native · " + mode);
        }); bar.addView(switchMode); root.addView(bar);
        FrameLayout area = new FrameLayout(this); area.setBackgroundColor(0xff000000);
        surface = new SurfaceView(this); area.addView(surface, new FrameLayout.LayoutParams(1, 1, Gravity.CENTER));
        area.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob) -> fitSurface());
        surface.setOnTouchListener((v,event) -> {
            if (input == null || !resumed || surface.getWidth() <= 0) return false;
            if (event.isFromSource(InputDevice.SOURCE_MOUSE)) return mouse(event);
            MotionEvent clamped = MotionEvent.obtain(event);
            // The view is aspect-fitted; clamp off-edge drags before encoding absolute coordinates.
            float max = EvdevEncoder.NORMALIZED_ABS_MAX;
            MotionEvent.PointerProperties[] props = new MotionEvent.PointerProperties[event.getPointerCount()];
            MotionEvent.PointerCoords[] coords = new MotionEvent.PointerCoords[event.getPointerCount()];
            for (int i=0;i<props.length;i++) {
                props[i]=new MotionEvent.PointerProperties(); coords[i]=new MotionEvent.PointerCoords();
                event.getPointerProperties(i,props[i]); event.getPointerCoords(i,coords[i]);
                coords[i].x=Math.max(0,Math.min(surface.getWidth()-1,coords[i].x));
                coords[i].y=Math.max(0,Math.min(surface.getHeight()-1,coords[i].y));
            }
            clamped.recycle();
            clamped=MotionEvent.obtain(event.getDownTime(),event.getEventTime(),event.getAction(),props.length,props,coords,event.getMetaState(),event.getButtonState(),event.getXPrecision(),event.getYPrecision(),event.getDeviceId(),event.getEdgeFlags(),event.getSource(),event.getFlags());
            input.sendTouchEvent(clamped,max/Math.max(1,surface.getWidth()-1),max/Math.max(1,surface.getHeight()-1));
            clamped.recycle(); return true;
        });
        surface.setOnGenericMotionListener((v,e) -> mouse(e));
        root.addView(area,new LinearLayout.LayoutParams(-1,0,1));
        EditText text = new EditText(this); text.setSingleLine(true); text.setHint("ASCII text → guest (Send)");
        LinearLayout typing = new LinearLayout(this); typing.addView(text,new LinearLayout.LayoutParams(0,-2,1));
        Button send = new Button(this); send.setText("Send"); send.setOnClickListener(v -> {
            if(input != null) for(char ch:text.getText().toString().toCharArray()) input.sendChar(ch);
            text.setText(""); surface.requestFocus();
        }); typing.addView(send); root.addView(typing);
        setContentView(root); surface.setFocusableInTouchMode(true); surface.requestFocus();
        registerReceiver(receiver,new IntentFilter(InputBroker.PACKAGE+".BROKER"),"android.permission.DUMP",null,Context.RECEIVER_EXPORTED);
    }

    private void fitSurface() {
        if(surface == null || !(surface.getParent() instanceof FrameLayout)) return;
        FrameLayout area=(FrameLayout)surface.getParent();
        int w=area.getWidth(),h=area.getHeight(); if(w<=0||h<=0)return;
        if((long)w*1514>(long)h*2272) w=h*2272/1514; else h=w*1514/2272;
        surface.setLayoutParams(new FrameLayout.LayoutParams(w,h,Gravity.CENTER));
    }

    private boolean mouse(MotionEvent e) {
        if(input==null||!resumed)return false;
        float x=e.getX(),y=e.getY();
        if(mode==InputMode.TABLET) input.sendAbsMove(InputPolicy.absolute(x,surface.getWidth()),InputPolicy.absolute(y,surface.getHeight()));
        else if(haveMouse) input.sendMouseMove(Math.round(x-lastX),Math.round(y-lastY));
        lastX=x;lastY=y;haveMouse=true;
        int next=e.getButtonState();
        int[] masks={MotionEvent.BUTTON_PRIMARY,MotionEvent.BUTTON_SECONDARY,MotionEvent.BUTTON_TERTIARY};
        short[] codes={EvdevEncoder.BTN_LEFT,EvdevEncoder.BTN_RIGHT,EvdevEncoder.BTN_MIDDLE};
        for(int i=0;i<3;i++)if(((buttons^next)&masks[i])!=0)input.sendPointerButton(codes[i],(next&masks[i])!=0);
        buttons=next;
        if(e.getActionMasked()==MotionEvent.ACTION_SCROLL)input.sendScroll(Math.round(e.getAxisValue(MotionEvent.AXIS_VSCROLL)),Math.round(e.getAxisValue(MotionEvent.AXIS_HSCROLL)));
        return true;
    }

    public boolean dispatchKeyEvent(KeyEvent e) {
        if(getCurrentFocus() instanceof EditText || e.getKeyCode()==KeyEvent.KEYCODE_BACK || e.getKeyCode()==KeyEvent.KEYCODE_VOLUME_DOWN || e.getKeyCode()==KeyEvent.KEYCODE_VOLUME_UP) return super.dispatchKeyEvent(e);
        if(input!=null && resumed && (e.getAction()==KeyEvent.ACTION_DOWN||e.getAction()==KeyEvent.ACTION_UP))
            if(input.sendKeyEvent(e.getKeyCode(),e.getAction()==KeyEvent.ACTION_DOWN)) return true;
        return super.dispatchKeyEvent(e);
    }
    private void release() { IInputBroker b=broker;String t=token; if(b!=null)io.execute(()->{try{b.releaseAll(t);}catch(Exception ignored){}}); buttons=0;haveMouse=false; }
    private void disconnect(){release();if(input!=null)input.close();if(source!=null)source.shutdown();input=null;source=null;broker=null;}
    public void onWindowFocusChanged(boolean focus){super.onWindowFocusChanged(focus);if(!focus)release();}
    protected void onResume(){super.onResume();resumed=true;}
    protected void onPause(){resumed=false;release();super.onPause();}
    protected void onDestroy(){unregisterReceiver(receiver);disconnect();io.shutdown();super.onDestroy();}
}
