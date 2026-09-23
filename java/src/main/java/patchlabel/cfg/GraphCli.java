package patchlabel.cfg;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.tree.ClassNode;
import org.objectweb.asm.tree.MethodNode;

public final class GraphCli {
    private GraphCli() {}

    public static void main(String[] args) throws Exception {
        if (args.length < 3) {
            System.err.println("Usage: GraphCli OUTPUT_JSON INCLUDE_FILE CLASSES_DIR...");
            System.exit(2);
        }
        Path output = Paths.get(args[0]);
        Path includeFile = Paths.get(args[1]);
        List<Path> roots = new ArrayList<Path>();
        for (int index = 2; index < args.length; index++) {
            roots.add(Paths.get(args[index]));
        }
        List<String> includes = readLines(includeFile);
        Set<Path> classFiles = new LinkedHashSet<Path>();
        List<String> missing = new ArrayList<String>();
        for (String className : includes) {
            List<Path> matches = findClassFiles(className, roots);
            if (matches.isEmpty()) {
                missing.add(className);
            }
            classFiles.addAll(matches);
        }

        List<CfgModel.MethodGraph> methods = new ArrayList<CfgModel.MethodGraph>();
        for (Path classFile : classFiles) {
            ClassNode classNode = new ClassNode();
            new ClassReader(Files.readAllBytes(classFile)).accept(classNode, ClassReader.SKIP_FRAMES);
            String className = classNode.name.replace('/', '.');
            for (MethodNode method : classNode.methods) {
                if ((method.access & (Opcodes.ACC_ABSTRACT | Opcodes.ACC_NATIVE)) == 0) {
                    methods.add(CfgBuilder.build(className, method));
                }
            }
        }
        Collections.sort(methods, new Comparator<CfgModel.MethodGraph>() {
            @Override
            public int compare(CfgModel.MethodGraph left, CfgModel.MethodGraph right) {
                return left.methodId.compareTo(right.methodId);
            }
        });
        Files.write(output, toJson(methods, missing).getBytes(StandardCharsets.UTF_8));
    }

    private static List<String> readLines(Path path) throws IOException {
        List<String> values = new ArrayList<String>();
        for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            String value = line.trim();
            if (!value.isEmpty()) {
                values.add(value);
            }
        }
        return values;
    }

    private static List<Path> findClassFiles(String className, List<Path> roots) throws IOException {
        String relative = className.replace('.', '/');
        int slash = relative.lastIndexOf('/');
        String directory = slash < 0 ? "" : relative.substring(0, slash);
        String simpleName = slash < 0 ? relative : relative.substring(slash + 1);
        List<Path> matches = new ArrayList<Path>();
        for (Path root : roots) {
            Path packageDirectory = directory.isEmpty() ? root : root.resolve(directory);
            Path mainClass = packageDirectory.resolve(simpleName + ".class");
            if (Files.isRegularFile(mainClass)) {
                matches.add(mainClass);
            }
            if (Files.isDirectory(packageDirectory)) {
                try (DirectoryStream<Path> stream = Files.newDirectoryStream(packageDirectory, simpleName + "$*.class")) {
                    for (Path nested : stream) {
                        matches.add(nested);
                    }
                }
            }
        }
        Collections.sort(matches);
        return matches;
    }

    private static String toJson(List<CfgModel.MethodGraph> methods, List<String> missing) {
        StringBuilder json = new StringBuilder(1024 * 64);
        json.append("{\"format\":\"bytecode-basic-block-cfg-v1\",");
        json.append("\"nodes\":[");
        boolean first = true;
        for (CfgModel.MethodGraph method : methods) {
            for (CfgModel.Node node : method.nodes) {
                if (!first) json.append(',');
                first = false;
                json.append('{');
                field(json, "id", node.id).append(',');
                field(json, "method_id", node.methodId).append(',');
                field(json, "class_name", node.className).append(',');
                field(json, "method_name", node.methodName).append(',');
                field(json, "descriptor", node.descriptor).append(',');
                numberField(json, "block_index", node.blockIndex).append(',');
                numberField(json, "start_instruction", node.startInstruction).append(',');
                numberField(json, "end_instruction", node.endInstruction).append(',');
                numberField(json, "start_line", node.startLine).append(',');
                numberField(json, "end_line", node.endLine).append(',');
                numberField(json, "instruction_count", node.instructionCount).append(',');
                json.append("\"virtual\":").append(node.virtual);
                json.append('}');
            }
        }
        json.append("],\"edges\":[");
        first = true;
        for (CfgModel.MethodGraph method : methods) {
            for (CfgModel.Edge edge : method.edges) {
                if (!first) json.append(',');
                first = false;
                json.append('{');
                field(json, "source", edge.source).append(',');
                field(json, "target", edge.target).append(',');
                field(json, "kind", edge.kind);
                json.append('}');
            }
        }
        json.append("],\"missing_classes\":[");
        first = true;
        for (String className : missing) {
            if (!first) json.append(',');
            first = false;
            quote(json, className);
        }
        json.append("]}\n");
        return json.toString();
    }

    private static StringBuilder field(StringBuilder json, String name, String value) {
        quote(json, name).append(':');
        return quote(json, value);
    }

    private static StringBuilder numberField(StringBuilder json, String name, int value) {
        quote(json, name).append(':').append(value);
        return json;
    }

    private static StringBuilder quote(StringBuilder json, String value) {
        json.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"': json.append("\\\""); break;
                case '\\': json.append("\\\\"); break;
                case '\n': json.append("\\n"); break;
                case '\r': json.append("\\r"); break;
                case '\t': json.append("\\t"); break;
                default:
                    if (character < 0x20) {
                        json.append(String.format("\\u%04x", (int) character));
                    } else {
                        json.append(character);
                    }
            }
        }
        return json.append('"');
    }
}

