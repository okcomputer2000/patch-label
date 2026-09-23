package patchlabel.trace;

import java.io.BufferedWriter;
import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;

public final class Recorder {
    private static final Object LOCK = new Object();
    private static final StringBuilder EVENTS = new StringBuilder(64 * 1024);
    private static volatile Path outputDirectory;
    private static volatile long maxEvents = 1_000_000L;
    private static long sequence = 0L;
    private static boolean configured = false;

    private Recorder() {}

    public static void configure(String directory, long configuredMaxEvents) {
        synchronized (LOCK) {
            if (configured) {
                return;
            }
            outputDirectory = Paths.get(directory);
            maxEvents = configuredMaxEvents;
            configured = true;
            Runtime.getRuntime().addShutdownHook(new Thread(new Runnable() {
                @Override
                public void run() {
                    flush();
                }
            }, "patch-label-trace-writer"));
        }
    }

    public static void hit(String nodeId) {
        synchronized (LOCK) {
            if (!configured || sequence >= maxEvents) {
                return;
            }
            sequence++;
            EVENTS.append(sequence)
                    .append('\t')
                    .append(Thread.currentThread().getId())
                    .append('\t')
                    .append(nodeId)
                    .append('\n');
        }
    }

    private static void flush() {
        synchronized (LOCK) {
            if (!configured || EVENTS.length() == 0) {
                return;
            }
            try {
                Files.createDirectories(outputDirectory);
                String runtimeName = ManagementFactory.getRuntimeMXBean().getName();
                String pid = runtimeName.contains("@") ? runtimeName.substring(0, runtimeName.indexOf('@')) : runtimeName;
                Path output = outputDirectory.resolve("trace-" + pid + "-" + System.nanoTime() + ".tsv");
                try (BufferedWriter writer = Files.newBufferedWriter(
                        output,
                        StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE_NEW,
                        StandardOpenOption.WRITE)) {
                    writer.write("# sequence\\tthread\\tnode_id\n");
                    writer.write(EVENTS.toString());
                }
            } catch (IOException exception) {
                System.err.println("patch-label: unable to write trace: " + exception.getMessage());
            }
        }
    }
}
