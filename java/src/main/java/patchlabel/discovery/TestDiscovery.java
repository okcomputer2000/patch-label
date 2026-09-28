package patchlabel.discovery;

import java.io.BufferedReader;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Set;
import java.util.TreeSet;
import junit.framework.TestCase;
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
                    boolean ownMethods = emit(runner.getDescription(), className);
                    // Some old JUnit 3 suite() methods return a different class's suite.
                    // Discover this class's public test methods so Defects4J can run them by method.
                    if (!ownMethods && TestCase.class.isAssignableFrom(testClass)) {
                        emitJUnit3Methods(testClass, className);
                    }
                } catch (Throwable throwable) {
                    System.out.println("ERROR\t" + clean(className) + "\t" + clean(throwable.toString()));
                }
            }
        }
    }

    private static boolean emit(Description description, String fallbackClassName) {
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
                return className.equals(fallbackClassName);
            }
            return false;
        }
        boolean ownMethods = false;
        for (Description child : description.getChildren()) {
            ownMethods |= emit(child, fallbackClassName);
        }
        return ownMethods;
    }

    private static void emitJUnit3Methods(Class<?> testClass, String className) {
        Set<String> methodNames = new TreeSet<String>();
        for (Method method : testClass.getMethods()) {
            int modifiers = method.getModifiers();
            if (method.getName().startsWith("test")
                    && method.getParameterTypes().length == 0
                    && method.getReturnType() == Void.TYPE
                    && Modifier.isPublic(modifiers)
                    && !Modifier.isStatic(modifiers)) {
                methodNames.add(method.getName());
            }
        }
        for (String methodName : methodNames) {
            System.out.println("TEST\t" + clean(className) + "\t" + clean(methodName)
                    + "\t" + clean(methodName + "(" + className + ")"));
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
