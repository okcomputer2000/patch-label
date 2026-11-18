package sample;

/** Execute variable-based primitive conversions; Python reads these same methods. */
public final class IntegralParameterOracle {
    static byte probeIntToByte(int x) { return (byte) x; }
    static short probeIntToShort(int x) { return (short) x; }
    static char probeIntToChar(int x) { return (char) x; }
    static long probeIntToLong(int x) { return (long) x; }
    static boolean probeIntByteIsZero(int x) { return (byte) x == 0; }
    static boolean probeIntNarrowRoundtrip(int x) { return (int) (byte) x == x; }
    static byte probeIntAlias(int x) { byte y = (byte) x; return y; }
    static int probeLongToInt(long x) { return (int) x; }
    static byte probeLongToByte(long x) { return (byte) x; }
    static char probeLongToChar(long x) { return (char) x; }
    static boolean probeLongNarrowRoundtrip(long x) { return (long) (int) x == x; }
    static int probeCharToInt(char x) { return (int) x; }
    static short probeCharToShort(char x) { return (short) x; }
    static boolean probeCharBelowZero(char x) { return x < '\0'; }
    static boolean probeCharNonnegative(char x) { return x >= 0; }
    static int probeByteToInt(byte x) { return (int) x; }
    static char probeByteToChar(byte x) { return (char) x; }
    static char probeShortToChar(short x) { return (char) x; }

    private static void write(String method, String inputType, long input,
                              String resultType, long result) {
        System.out.println(method + "\t" + inputType + "\t" + input
                           + "\t" + resultType + "\t" + result);
    }

    private static void emit(String method, String inputType, long input, byte value) {
        write(method, inputType, input, "byte", value);
    }
    private static void emit(String method, String inputType, long input, short value) {
        write(method, inputType, input, "short", value);
    }
    private static void emit(String method, String inputType, long input, char value) {
        write(method, inputType, input, "char", value);
    }
    private static void emit(String method, String inputType, long input, int value) {
        write(method, inputType, input, "int", value);
    }
    private static void emit(String method, String inputType, long input, long value) {
        write(method, inputType, input, "long", value);
    }
    private static void emit(String method, String inputType, long input, boolean value) {
        write(method, inputType, input, "boolean", value ? 1 : 0);
    }

    public static void main(String[] args) {
        int[] ints = {
            Integer.MIN_VALUE, -65537, -65536, -32769, -32768, -257, -256,
            -129, -128, -1, 0, 1, 65, 127, 128, 255, 256, 257,
            32767, 32768, 65535, 65536, Integer.MAX_VALUE
        };
        for (int input : ints) {
            emit("probeIntToByte", "int", input, probeIntToByte(input));
            emit("probeIntToShort", "int", input, probeIntToShort(input));
            emit("probeIntToChar", "int", input, probeIntToChar(input));
            emit("probeIntToLong", "int", input, probeIntToLong(input));
            emit("probeIntByteIsZero", "int", input, probeIntByteIsZero(input));
            emit("probeIntNarrowRoundtrip", "int", input, probeIntNarrowRoundtrip(input));
            emit("probeIntAlias", "int", input, probeIntAlias(input));
        }
        long[] longs = {
            Long.MIN_VALUE, -4294967297L, -4294967296L, -2147483649L,
            -2147483648L, -65537L, -65536L, -129L, -128L, -1L,
            0L, 1L, 127L, 128L, 255L, 256L, 65535L, 65536L,
            2147483647L, 2147483648L, 4294967295L, 4294967296L,
            9007199254740993L, Long.MAX_VALUE
        };
        for (long input : longs) {
            emit("probeLongToInt", "long", input, probeLongToInt(input));
            emit("probeLongToByte", "long", input, probeLongToByte(input));
            emit("probeLongToChar", "long", input, probeLongToChar(input));
            emit("probeLongNarrowRoundtrip", "long", input, probeLongNarrowRoundtrip(input));
        }
        char[] chars = { 0, 1, 65, 127, 128, 255, 256, 32767, 32768, 65535 };
        for (char input : chars) {
            emit("probeCharToInt", "char", input, probeCharToInt(input));
            emit("probeCharToShort", "char", input, probeCharToShort(input));
            emit("probeCharBelowZero", "char", input, probeCharBelowZero(input));
            emit("probeCharNonnegative", "char", input, probeCharNonnegative(input));
        }
        byte[] bytes = { -128, -127, -1, 0, 1, 65, 126, 127 };
        for (byte input : bytes) {
            emit("probeByteToInt", "byte", input, probeByteToInt(input));
            emit("probeByteToChar", "byte", input, probeByteToChar(input));
        }
        short[] shorts = { -32768, -32767, -129, -128, -1, 0, 1, 127, 128, 255, 32767 };
        for (short input : shorts) {
            emit("probeShortToChar", "short", input, probeShortToChar(input));
        }
    }
}
