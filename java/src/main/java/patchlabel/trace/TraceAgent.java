package patchlabel.trace;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.IllegalClassFormatException;
import java.lang.instrument.Instrumentation;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.ProtectionDomain;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;
import java.util.logging.Handler;
import java.util.logging.Level;
import java.util.logging.LogRecord;
import java.util.logging.Logger;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.tree.AbstractInsnNode;
import org.objectweb.asm.tree.ClassNode;
import org.objectweb.asm.tree.FieldInsnNode;
import org.objectweb.asm.tree.InsnList;
import org.objectweb.asm.tree.LdcInsnNode;
import org.objectweb.asm.tree.MethodInsnNode;
import org.objectweb.asm.tree.MethodNode;
import patchlabel.cfg.CfgBuilder;
import patchlabel.cfg.CfgModel;

public final class TraceAgent {
    private static final String TRACE_LOGGER_NAME = "patchlabel.trace.events";
    private static final Logger TRACE_LOGGER = Logger.getLogger(TRACE_LOGGER_NAME);

    private TraceAgent() {}

    public static void premain(String agentArgs, Instrumentation instrumentation) {
        configure(agentArgs, instrumentation);
    }

    public static void agentmain(String agentArgs, Instrumentation instrumentation) {
        configure(agentArgs, instrumentation);
    }

    private static void configure(String agentArgs, Instrumentation instrumentation) {
        if (agentArgs == null || agentArgs.trim().isEmpty()) {
            throw new IllegalArgumentException("patch-label agent requires a properties file path");
        }
        Properties properties = new Properties();
        Path propertiesPath = Paths.get(agentArgs);
        try (InputStream stream = Files.newInputStream(propertiesPath)) {
            properties.load(stream);
        } catch (IOException exception) {
            throw new IllegalArgumentException("Cannot load agent properties: " + propertiesPath, exception);
        }
        String outputDirectory = require(properties, "outputDir");
        Path includesFile = Paths.get(require(properties, "includesFile"));
        long maxEvents = Long.parseLong(properties.getProperty("maxEvents", "1000000"));
        Recorder.configure(outputDirectory, maxEvents);
        // Isolated project class loaders can call JDK logging without loading agent classes.
        TRACE_LOGGER.setUseParentHandlers(false);
        TRACE_LOGGER.setLevel(Level.ALL);
        Handler handler = new Handler() {
            @Override
            public void publish(LogRecord record) {
                if (record != null) {
                    Recorder.hit(record.getMessage());
                }
            }

            @Override
            public void flush() {}

            @Override
            public void close() {}
        };
        handler.setLevel(Level.ALL);
        TRACE_LOGGER.addHandler(handler);
        String command = System.getProperty("sun.java.command", "");
        // Defects4J's compile.tests can execute project code before the test begins.
        if (!command.endsWith(" compile.tests")) {
            instrumentation.addTransformer(new Transformer(readIncludes(includesFile)), false);
        }
    }

    private static String require(Properties properties, String key) {
        String value = properties.getProperty(key);
        if (value == null || value.trim().isEmpty()) {
            throw new IllegalArgumentException("Missing agent property: " + key);
        }
        return value;
    }

    private static List<String> readIncludes(Path path) {
        List<String> includes = new ArrayList<String>();
        try (BufferedReader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            String line;
            while ((line = reader.readLine()) != null) {
                String value = line.trim().replace('.', '/');
                if (!value.isEmpty()) {
                    includes.add(value);
                }
            }
        } catch (IOException exception) {
            throw new IllegalArgumentException("Cannot load agent include list: " + path, exception);
        }
        return includes;
    }

    private static final class Transformer implements ClassFileTransformer {
        private final List<String> includes;

        Transformer(List<String> includes) {
            this.includes = includes;
        }

        @Override
        public byte[] transform(
                ClassLoader loader,
                String className,
                Class<?> classBeingRedefined,
                ProtectionDomain protectionDomain,
                byte[] classfileBuffer) throws IllegalClassFormatException {
            if (className == null || !isIncluded(className)) {
                return null;
            }
            try {
                ClassReader reader = new ClassReader(classfileBuffer);
                ClassNode classNode = new ClassNode();
                reader.accept(classNode, 0);
                String dottedClassName = classNode.name.replace('/', '.');
                for (MethodNode method : classNode.methods) {
                    if ((method.access & (Opcodes.ACC_ABSTRACT | Opcodes.ACC_NATIVE)) != 0) {
                        continue;
                    }
                    instrument(dottedClassName, method);
                }
                ClassWriter writer = new ClassWriter(reader, ClassWriter.COMPUTE_MAXS);
                classNode.accept(writer);
                return writer.toByteArray();
            } catch (RuntimeException exception) {
                IllegalClassFormatException wrapped = new IllegalClassFormatException(
                        "Unable to instrument " + className + ": " + exception.getMessage());
                wrapped.initCause(exception);
                throw wrapped;
            }
        }

        private boolean isIncluded(String className) {
            for (String include : includes) {
                if (className.equals(include) || className.startsWith(include + "$")) {
                    return true;
                }
            }
            return false;
        }

        private static void instrument(String className, MethodNode method) {
            AbstractInsnNode[] original = method.instructions.toArray();
            CfgModel.MethodGraph graph = CfgBuilder.build(className, method);
            String methodId = className + "#" + method.name + method.desc;
            for (CfgModel.Node node : graph.nodes) {
                if (node.virtual || node.startInstruction < 0 || node.startInstruction >= original.length) {
                    continue;
                }
                InsnList probe = new InsnList();
                if (node.blockIndex == 0) {
                    addHit(probe, methodId + ":ENTRY");
                }
                addHit(probe, node.id);
                method.instructions.insertBefore(original[node.startInstruction], probe);
            }
            for (AbstractInsnNode instruction : original) {
                if (isTerminal(instruction.getOpcode())) {
                    InsnList probe = new InsnList();
                    addHit(probe, methodId + ":EXIT");
                    method.instructions.insertBefore(instruction, probe);
                }
            }
        }

        private static void addHit(InsnList probe, String nodeId) {
            probe.add(new LdcInsnNode(TRACE_LOGGER_NAME));
            probe.add(new MethodInsnNode(
                    Opcodes.INVOKESTATIC,
                    "java/util/logging/Logger",
                    "getLogger",
                    "(Ljava/lang/String;)Ljava/util/logging/Logger;",
                    false));
            probe.add(new FieldInsnNode(
                    Opcodes.GETSTATIC,
                    "java/util/logging/Level",
                    "FINE",
                    "Ljava/util/logging/Level;"));
            probe.add(new LdcInsnNode(nodeId));
            probe.add(new MethodInsnNode(
                    Opcodes.INVOKEVIRTUAL,
                    "java/util/logging/Logger",
                    "log",
                    "(Ljava/util/logging/Level;Ljava/lang/String;)V",
                    false));
        }

        private static boolean isTerminal(int opcode) {
            return opcode == Opcodes.IRETURN
                    || opcode == Opcodes.LRETURN
                    || opcode == Opcodes.FRETURN
                    || opcode == Opcodes.DRETURN
                    || opcode == Opcodes.ARETURN
                    || opcode == Opcodes.RETURN
                    || opcode == Opcodes.ATHROW;
        }
    }
}
