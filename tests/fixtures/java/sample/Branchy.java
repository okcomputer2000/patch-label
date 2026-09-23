package sample;

public final class Branchy {
    private Branchy() {}

    public static int classify(int value) {
        if (value > 0) {
            return 1;
        }
        return -1;
    }

    public static void main(String[] args) {
        System.out.println(classify(Integer.parseInt(args[0])));
    }
}
