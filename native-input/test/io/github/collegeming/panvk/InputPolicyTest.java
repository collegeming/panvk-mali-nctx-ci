package io.github.collegeming.panvk;
public final class InputPolicyTest {
    private static void check(boolean value) { if (!value) throw new AssertionError(); }
    public static void main(String[] args) {
        check(InputPolicy.authorized(10001,10001,"token","token"));
        check(!InputPolicy.authorized(10002,10001,"token","token"));
        check(!InputPolicy.authorized(10001,10001,"token","other"));
        check(!InputPolicy.authorized(10001,10001,"token",null));
        for(int channel=0;channel<4;channel++) check(InputPolicy.validBatch(channel,new byte[16]));
        check(!InputPolicy.validBatch(-1,new byte[8]));
        check(!InputPolicy.validBatch(4,new byte[8]));
        check(!InputPolicy.validBatch(0,new byte[7]));
        check(!InputPolicy.validBatch(0,new byte[16392]));
        check(!InputPolicy.validBatch(0,null));
        check(InputPolicy.absolute(-12,1514)==0);
        check(InputPolicy.absolute(1513,1514)==32767);
        check(InputPolicy.absolute(99999,1514)==32767);
        check(InputPolicy.absolute(Float.NaN,1514)==0);
        check(InputPolicy.absolute(0,0)==0);
        System.out.println("InputPolicy PASS: UID/token, channels, framing, coordinate boundaries");
    }
}
