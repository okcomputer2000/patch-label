package sample;

/** Actual Java values for the intentionally limited integral-expression model. */
public final class IntegralOracle {
    private static void write(String id, String type, long value) {
        System.out.println(id + "\t" + type + "\t" + value);
    }

    private static void emit(String id, byte value) { write(id, "byte", value); }
    private static void emit(String id, short value) { write(id, "short", value); }
    private static void emit(String id, char value) { write(id, "char", value); }
    private static void emit(String id, int value) { write(id, "int", value); }
    private static void emit(String id, long value) { write(id, "long", value); }
    private static void emitBoolean(String id, boolean value) {
        write(id, "boolean", value ? 1 : 0);
    }

    public static void main(String[] args) {
        emit("char_digit", '0');
        emit("char_underscore", '_');
        emit("char_cjk", '中');
        emit("char_greek", 'Ω');
        emit("char_accent", 'é');
        emit("char_backspace", '\b');
        emit("char_tab", '\t');
        emit("char_newline", '\n');
        emit("char_formfeed", '\f');
        emit("char_return", '\r');
        emit("char_double_quote", '\"');
        emit("char_single_quote", '\'');
        emit("char_backslash", '\\');
        emit("char_octal_zero", '\0');
        emit("char_octal_one_digit", '\7');
        emit("char_octal_two_digits", '\77');
        emit("char_octal_three_digits", '\377');
        emit("char_octal_space", '\40');
        emit("int_zero", 0);
        emit("int_max", 2147483647);
        emit("int_min", -2147483648);
        emit("long_max", 9223372036854775807L);
        emit("long_min", -9223372036854775808L);
        emit("int_hex_negative", 0xffff_ffff);
        emit("int_hex_min", 0x8000_0000);
        emit("int_octal_negative", 0377_7777_7777);
        emit("int_binary_negative", 0b1111_1111_1111_1111_1111_1111_1111_1111);
        emit("long_hex_negative", 0xffff_ffff_ffff_ffffL);
        emit("long_hex_min", 0x8000_0000_0000_0000L);
        emit("long_decimal_suffix", 1l);
        emit("byte_zero", (byte) 256);
        emit("byte_sign", (byte) 128);
        emit("byte_negative_wrap", (byte) -129);
        emit("short_sign", (short) 65535);
        emit("short_zero", (short) 65536);
        emit("char_negative_wrap", (char) -1);
        emit("char_zero", (char) 65536);
        emit("int_from_long", (int) 2147483648L);
        emit("long_from_signed_hex", (long) 0xffff_ffff);
        emit("long_from_unsigned_magnitude", (long) 0xffff_ffffL);
        emit("nested_signed_byte", (long) (byte) 255);
        emit("nested_unsigned_char", (int) (char) (byte) -1);
        emit("nested_char_to_byte", (byte) (char) 65535);
        emit("byte_arithmetic", (byte) (127 + 1));
        emit("char_to_long", (long) '中');
        emit("int_add_overflow", 2147483647 + 1);
        emit("int_subtract_overflow", -2147483648 - 1);
        emit("int_multiply_overflow", -2147483648 * -1);
        emit("long_add_overflow", 9223372036854775807L + 1L);
        emit("long_subtract_overflow", -9223372036854775808L - 1L);
        emit("char_multiply_overflow", (char) 65535 * 65535);
        emit("char_add_promotion", (char) 65535 + (char) 65535);
        emit("byte_add_promotion", (byte) 127 + (byte) 1);
        emit("mixed_long_promotion", 2147483647 + 1L);
        emit("negative_division", -7 / 3);
        emit("negative_divisor", 7 / -3);
        emit("both_negative_division", -7 / -3);
        emit("negative_remainder", -7 % 3);
        emit("negative_divisor_remainder", 7 % -3);
        emit("both_negative_remainder", -7 % -3);
        emit("int_min_division", -2147483648 / -1);
        emit("long_min_division", -9223372036854775808L / -1L);
        emit("int_min_remainder", -2147483648 % -1);
        emit("unary_char_promotion", -'A');
        emit("unary_byte_promotion", +(byte) 1);
        emit("unary_int_min_overflow", -(-2147483648));
        emit("unary_long_min_overflow", -(-9223372036854775808L));
        emit("parentheses", (((char) 65)));
        emit("left_associative_subtraction", 10 - 3 - 2);
        emit("nested_division_cast", (byte) ((1000L / -3L) % 256L));
        emitBoolean("char_vs_negative_hex", 'A' > 0xffff_ffff);
        emitBoolean("char_vs_hex_min", 'A' < 0x8000_0000);
        emitBoolean("char_vs_signed_short", (char) 65535 > (short) 65535);
        emitBoolean("cast_hex_equality", (long) 0xffff_ffff == -1L);
    }
}
