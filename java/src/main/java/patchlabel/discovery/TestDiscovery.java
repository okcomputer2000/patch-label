package patchlabel.discovery;

import java.io.BufferedReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import org.junit.runner.Description;
import org.junit.runner.Request;
import org.junit.runner.Runner;

public final class TestDiscovery {
    private TestDiscovery() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            System.err.println("Usage: TestDiscovery TEST_CLASS_LIST");
            System.exit(2);
        }
        Path classes = Paths.get(args[0]);
        ClassLoader loader = Thread.currentThread().getContextClassLoader();
        try (BufferedReader reader = Files.newBufferedReader(classes, StandardCharsets.UTF_8)) {
            String line;
            while ((line = reader.readLine()) != null) {
                String className = line.trim();
                if (className.isEmpty()) {
                    continue;
                }
                try {
                    Class<?> testClass = Class.forName(className, false, loader);
                    Runner runner = Request.aClass(testClass).getRunner();
                    emit(runner.getDescription(), className);
                } catch (Throwable throwable) {
                    System.out.println("ERROR\t" + clean(className) + "\t" + clean(throwable.toString()));
                }
            }
        }
    }

    private static void emit(Description description, String fallbackClassName) {
        if (description.isTest()) {
            String className = description.getClassName();
            if (className == null || className.isEmpty()) {
                className = fallbackClassName;
            }
            String methodName = description.getMethodName();
            if (methodName == null || methodName.isEmpty()) {
                methodName = inferMethod(description.getDisplayName());
            }
            if (methodName != null && !methodName.isEmpty()) {
                System.out.println(
                        "TEST\t" + clean(className) + "\t" + clean(methodName) + "\t" + clean(description.getDisplayName()));
            }
            return;
        }
        for (Description child : description.getChildren()) {
            emit(child, fallbackClassName);
        }
    }

    private static String inferMethod(String displayName) {
        int opening = displayName.indexOf('(');
        return opening > 0 ? displayName.substring(0, opening) : displayName;
    }

    private static String clean(String value) {
        return value.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ');
    }
}
