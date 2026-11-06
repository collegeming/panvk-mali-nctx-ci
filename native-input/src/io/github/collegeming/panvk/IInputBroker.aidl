package io.github.collegeming.panvk;
interface IInputBroker {
    IBinder display(String token);
    boolean write(String token, int channel, in byte[] bytes);
    void releaseAll(String token);
    void watch(String token, IBinder client);
}
