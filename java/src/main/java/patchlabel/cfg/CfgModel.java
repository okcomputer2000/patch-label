package patchlabel.cfg;

import java.util.ArrayList;
import java.util.List;

public final class CfgModel {
    private CfgModel() {}

    public static final class Node {
        public final String id;
        public final String methodId;
        public final String className;
        public final String methodName;
        public final String descriptor;
        public final int blockIndex;
        public final int startInstruction;
        public final int endInstruction;
        public final int startLine;
        public final int endLine;
        public final int instructionCount;
        public final boolean virtual;

        Node(
                String id,
                String methodId,
                String className,
                String methodName,
                String descriptor,
                int blockIndex,
                int startInstruction,
                int endInstruction,
                int startLine,
                int endLine,
                int instructionCount,
                boolean virtual) {
            this.id = id;
            this.methodId = methodId;
            this.className = className;
            this.methodName = methodName;
            this.descriptor = descriptor;
            this.blockIndex = blockIndex;
            this.startInstruction = startInstruction;
            this.endInstruction = endInstruction;
            this.startLine = startLine;
            this.endLine = endLine;
            this.instructionCount = instructionCount;
            this.virtual = virtual;
        }
    }

    public static final class Edge {
        public final String source;
        public final String target;
        public final String kind;

        Edge(String source, String target, String kind) {
            this.source = source;
            this.target = target;
            this.kind = kind;
        }
    }

    public static final class MethodGraph {
        public final String methodId;
        public final List<Node> nodes = new ArrayList<Node>();
        public final List<Edge> edges = new ArrayList<Edge>();

        MethodGraph(String methodId) {
            this.methodId = methodId;
        }
    }
}

