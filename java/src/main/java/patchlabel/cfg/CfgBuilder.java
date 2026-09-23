package patchlabel.cfg;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.tree.AbstractInsnNode;
import org.objectweb.asm.tree.JumpInsnNode;
import org.objectweb.asm.tree.LabelNode;
import org.objectweb.asm.tree.LineNumberNode;
import org.objectweb.asm.tree.LookupSwitchInsnNode;
import org.objectweb.asm.tree.MethodNode;
import org.objectweb.asm.tree.TableSwitchInsnNode;
import org.objectweb.asm.tree.TryCatchBlockNode;

public final class CfgBuilder {
    private CfgBuilder() {}

    public static CfgModel.MethodGraph build(String className, MethodNode method) {
        String methodId = className + "#" + method.name + method.desc;
        CfgModel.MethodGraph graph = new CfgModel.MethodGraph(methodId);
        AbstractInsnNode[] instructions = method.instructions.toArray();
        int first = nextExecutable(instructions, 0);
        addVirtualNodes(graph, className, method, methodId);
        if (first < 0) {
            addEdge(graph, methodId + ":ENTRY", methodId + ":EXIT", "empty");
            return graph;
        }

        Map<LabelNode, Integer> labels = labelTargets(instructions);
        TreeSet<Integer> leaders = new TreeSet<Integer>();
        leaders.add(first);
        for (int index = 0; index < instructions.length; index++) {
            AbstractInsnNode instruction = instructions[index];
            if (instruction instanceof JumpInsnNode) {
                addTarget(leaders, labels.get(((JumpInsnNode) instruction).label));
                addTarget(leaders, nextExecutable(instructions, index + 1));
            } else if (instruction instanceof LookupSwitchInsnNode) {
                LookupSwitchInsnNode lookup = (LookupSwitchInsnNode) instruction;
                addTarget(leaders, labels.get(lookup.dflt));
                for (LabelNode label : lookup.labels) {
                    addTarget(leaders, labels.get(label));
                }
                addTarget(leaders, nextExecutable(instructions, index + 1));
            } else if (instruction instanceof TableSwitchInsnNode) {
                TableSwitchInsnNode table = (TableSwitchInsnNode) instruction;
                addTarget(leaders, labels.get(table.dflt));
                for (LabelNode label : table.labels) {
                    addTarget(leaders, labels.get(label));
                }
                addTarget(leaders, nextExecutable(instructions, index + 1));
            } else if (isTerminal(instruction.getOpcode())) {
                addTarget(leaders, nextExecutable(instructions, index + 1));
            }
        }
        for (TryCatchBlockNode tryCatch : method.tryCatchBlocks) {
            addTarget(leaders, labels.get(tryCatch.handler));
            addTarget(leaders, labels.get(tryCatch.start));
        }

        List<Integer> starts = new ArrayList<Integer>(leaders);
        Map<Integer, Integer> instructionToBlock = new HashMap<Integer, Integer>();
        Map<Integer, Integer> lineByInstruction = instructionLines(instructions);
        for (int blockIndex = 0; blockIndex < starts.size(); blockIndex++) {
            int start = starts.get(blockIndex);
            int end = blockIndex + 1 < starts.size() ? starts.get(blockIndex + 1) : instructions.length;
            int instructionCount = 0;
            int startLine = Integer.MAX_VALUE;
            int endLine = -1;
            for (int index = start; index < end; index++) {
                if (instructions[index].getOpcode() >= 0) {
                    instructionToBlock.put(index, blockIndex);
                    instructionCount++;
                    Integer line = lineByInstruction.get(index);
                    if (line != null && line > 0) {
                        startLine = Math.min(startLine, line);
                        endLine = Math.max(endLine, line);
                    }
                }
            }
            graph.nodes.add(
                    new CfgModel.Node(
                            methodId + ":B" + blockIndex,
                            methodId,
                            className,
                            method.name,
                            method.desc,
                            blockIndex,
                            start,
                            end,
                            startLine == Integer.MAX_VALUE ? -1 : startLine,
                            endLine,
                            instructionCount,
                            false));
        }

        addEdge(graph, methodId + ":ENTRY", methodId + ":B0", "entry");
        for (int blockIndex = 0; blockIndex < starts.size(); blockIndex++) {
            int start = starts.get(blockIndex);
            int end = blockIndex + 1 < starts.size() ? starts.get(blockIndex + 1) : instructions.length;
            int last = previousExecutable(instructions, end - 1, start);
            if (last < 0) {
                continue;
            }
            String source = methodId + ":B" + blockIndex;
            AbstractInsnNode instruction = instructions[last];
            int opcode = instruction.getOpcode();
            if (instruction instanceof JumpInsnNode) {
                Integer targetIndex = labels.get(((JumpInsnNode) instruction).label);
                addBlockEdge(graph, source, targetIndex, instructionToBlock, methodId, "jump");
                if (opcode != Opcodes.GOTO && opcode != Opcodes.JSR) {
                    addBlockEdge(
                            graph,
                            source,
                            nextExecutable(instructions, last + 1),
                            instructionToBlock,
                            methodId,
                            "fallthrough");
                } else if (opcode == Opcodes.JSR) {
                    addBlockEdge(
                            graph,
                            source,
                            nextExecutable(instructions, last + 1),
                            instructionToBlock,
                            methodId,
                            "subroutine-return");
                }
            } else if (instruction instanceof LookupSwitchInsnNode) {
                LookupSwitchInsnNode lookup = (LookupSwitchInsnNode) instruction;
                addBlockEdge(graph, source, labels.get(lookup.dflt), instructionToBlock, methodId, "switch-default");
                for (LabelNode label : lookup.labels) {
                    addBlockEdge(graph, source, labels.get(label), instructionToBlock, methodId, "switch-case");
                }
            } else if (instruction instanceof TableSwitchInsnNode) {
                TableSwitchInsnNode table = (TableSwitchInsnNode) instruction;
                addBlockEdge(graph, source, labels.get(table.dflt), instructionToBlock, methodId, "switch-default");
                for (LabelNode label : table.labels) {
                    addBlockEdge(graph, source, labels.get(label), instructionToBlock, methodId, "switch-case");
                }
            } else if (isTerminal(opcode) || opcode == Opcodes.RET) {
                addEdge(graph, source, methodId + ":EXIT", opcode == Opcodes.ATHROW ? "throw" : "exit");
            } else {
                int next = nextExecutable(instructions, last + 1);
                if (next >= 0) {
                    addBlockEdge(graph, source, next, instructionToBlock, methodId, "fallthrough");
                } else {
                    addEdge(graph, source, methodId + ":EXIT", "exit");
                }
            }
        }
        addExceptionEdges(graph, method, labels, instructionToBlock, starts, instructions.length, methodId);
        return graph;
    }

    private static void addVirtualNodes(
            CfgModel.MethodGraph graph, String className, MethodNode method, String methodId) {
        graph.nodes.add(new CfgModel.Node(
                methodId + ":ENTRY", methodId, className, method.name, method.desc,
                -1, -1, -1, -1, -1, 0, true));
        graph.nodes.add(new CfgModel.Node(
                methodId + ":EXIT", methodId, className, method.name, method.desc,
                -1, -1, -1, -1, -1, 0, true));
    }

    private static Map<LabelNode, Integer> labelTargets(AbstractInsnNode[] instructions) {
        Map<LabelNode, Integer> labels = new HashMap<LabelNode, Integer>();
        for (int index = 0; index < instructions.length; index++) {
            if (instructions[index] instanceof LabelNode) {
                labels.put((LabelNode) instructions[index], nextExecutable(instructions, index + 1));
            }
        }
        return labels;
    }

    private static Map<Integer, Integer> instructionLines(AbstractInsnNode[] instructions) {
        Map<Integer, Integer> result = new HashMap<Integer, Integer>();
        int line = -1;
        for (int index = 0; index < instructions.length; index++) {
            if (instructions[index] instanceof LineNumberNode) {
                line = ((LineNumberNode) instructions[index]).line;
            } else if (instructions[index].getOpcode() >= 0) {
                result.put(index, line);
            }
        }
        return result;
    }

    private static void addExceptionEdges(
            CfgModel.MethodGraph graph,
            MethodNode method,
            Map<LabelNode, Integer> labels,
            Map<Integer, Integer> instructionToBlock,
            List<Integer> starts,
            int instructionLength,
            String methodId) {
        for (TryCatchBlockNode tryCatch : method.tryCatchBlocks) {
            Integer tryStart = labels.get(tryCatch.start);
            Integer tryEnd = labels.get(tryCatch.end);
            Integer handler = labels.get(tryCatch.handler);
            if (tryStart == null || handler == null || tryStart < 0 || handler < 0) {
                continue;
            }
            int exclusiveEnd = tryEnd == null || tryEnd < 0 ? instructionLength : tryEnd;
            Integer handlerBlock = instructionToBlock.get(handler);
            if (handlerBlock == null) {
                continue;
            }
            for (int blockIndex = 0; blockIndex < starts.size(); blockIndex++) {
                int blockStart = starts.get(blockIndex);
                int blockEnd = blockIndex + 1 < starts.size() ? starts.get(blockIndex + 1) : instructionLength;
                if (blockStart < exclusiveEnd && blockEnd > tryStart) {
                    addEdge(
                            graph,
                            methodId + ":B" + blockIndex,
                            methodId + ":B" + handlerBlock,
                            tryCatch.type == null ? "exception:any" : "exception:" + tryCatch.type.replace('/', '.'));
                }
            }
        }
    }

    private static void addBlockEdge(
            CfgModel.MethodGraph graph,
            String source,
            Integer targetInstruction,
            Map<Integer, Integer> instructionToBlock,
            String methodId,
            String kind) {
        if (targetInstruction == null || targetInstruction < 0) {
            return;
        }
        Integer targetBlock = instructionToBlock.get(targetInstruction);
        if (targetBlock != null) {
            addEdge(graph, source, methodId + ":B" + targetBlock, kind);
        }
    }

    private static void addEdge(CfgModel.MethodGraph graph, String source, String target, String kind) {
        for (CfgModel.Edge edge : graph.edges) {
            if (edge.source.equals(source) && edge.target.equals(target) && edge.kind.equals(kind)) {
                return;
            }
        }
        graph.edges.add(new CfgModel.Edge(source, target, kind));
    }

    private static void addTarget(Set<Integer> leaders, Integer target) {
        if (target != null && target >= 0) {
            leaders.add(target);
        }
    }

    private static int nextExecutable(AbstractInsnNode[] instructions, int start) {
        for (int index = Math.max(0, start); index < instructions.length; index++) {
            if (instructions[index].getOpcode() >= 0) {
                return index;
            }
        }
        return -1;
    }

    private static int previousExecutable(AbstractInsnNode[] instructions, int start, int minimum) {
        for (int index = start; index >= minimum; index--) {
            if (instructions[index].getOpcode() >= 0) {
                return index;
            }
        }
        return -1;
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
