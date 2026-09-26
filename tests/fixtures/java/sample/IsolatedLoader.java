package sample;

import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Paths;

public final class IsolatedLoader {
    public static void main(String[] args) throws Exception {
        URL classes = Paths.get(args[0]).toUri().toURL();
        try (URLClassLoader loader = new URLClassLoader(new URL[] {classes}, null)) {
            Class<?> branchy = Class.forName("sample.Branchy", true, loader);
            branchy.getMethod("main", String[].class).invoke(null, (Object) new String[] {"2"});
        }
    }
}
