"""Evidence-bearing values and conservative bindings in complete Java methods.

This module resolves declarations, not arbitrary Java execution. In particular it
never invents a solver value for a name it cannot resolve. ``Binding.reason`` is
nonempty on rejection; callers must keep such expressions unsupported.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import Any


INTEGRAL_TYPES = frozenset({"byte", "short", "char", "int", "long"})
_JVM_PRIMITIVES = {"boolean": "Z", "byte": "B", "short": "S", "char": "C",
                   "int": "I", "long": "J", "float": "F", "double": "D", "void": "V"}
_JAVA_LANG_TYPES = frozenset({
    "Object", "String", "CharSequence", "StringBuilder", "StringBuffer",
    "Boolean", "Byte", "Short", "Character", "Integer", "Long", "Float", "Double",
    "Number", "Throwable", "Exception", "RuntimeException", "Error", "Class", "Enum",
    "Comparable", "Iterable", "Runnable", "Cloneable", "Appendable", "AutoCloseable",
    "Math", "StrictMath", "System", "Thread", "ClassLoader", "Package", "Void",
})
_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"


@dataclass(slots=True)
class ExprResult:
    value: Any = None
    java_type: str | None = None
    normal_when: Any = True
    unsupported: set[str] = field(default_factory=set)
    model_evidence: list[dict[str, Any]] = field(default_factory=list)
    domain_constraints: list[Any] = field(default_factory=list)
    exceptions: list[Any] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Binding:
    java_type: str | None = None
    initializer_node: Any = None
    parameter_key: str | None = None
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


def _walk(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.named_children))


def _ancestor(node, kind):
    current = node.parent
    while current is not None:
        if current.type == kind:
            return current
        current = current.parent
    return None


def _same_node(left, right):
    return left is not None and right is not None and left == right


def _compact(text):
    return re.sub(r"\s+", "", text)


class ModelContext:
    """Read-only resolution for unchanged integral parameters and local aliases.

    Parameters have stable keys by package/class/method/signature/ordinal, not
    by their names or source positions. Locals must be uniquely declared in the
    entire method, never assigned or updated, initialized in the method-entry
    declaration prefix, and used in that same directly enclosing block. These
    restrictions intentionally
    reject some valid Java rather than infer a value from an incomplete history.
    """

    def __init__(self, node, source: bytes, source_path: str | None = None):
        self.source = source
        self.source_path = source_path
        self.source_sha256 = hashlib.sha256(source).hexdigest()
        self.method_node = None
        self.method_key = None
        self.signature_key = None
        self.reason = ""
        self._parameters = {}
        self._declarations = {}
        self._writes = set()
        self._root_node = None
        self._package_name = ""
        self._owner_names = []
        self._parameter_types = []
        self._method_name = None
        self._prepare(node)

    @classmethod
    def from_node(cls, node, source: bytes, source_path: str | None = None):
        return cls(node, source, source_path)

    def _text(self, node):
        return self.source[node.start_byte:node.end_byte].decode("utf-8")

    def _prepare(self, node):
        if node is None:
            self.reason = "missing_source_node"
            return
        root = node
        while root.parent is not None:
            root = root.parent
        if (root.type != "program" or root.has_error or root.is_missing
                or root.start_byte != 0 or root.end_byte != len(self.source)
                or root.text != self.source):
            self.reason = "incomplete_or_mismatched_source"
            return
        try:
            self.source.decode("utf-8")
        except UnicodeDecodeError:
            self.reason = "invalid_utf8_source"
            return
        # Unicode escapes are processed before tokenization by Java. Tree-sitter
        # does not provide that preprocessing contract here.
        if re.search(rb"\\u+[0-9a-fA-F]{4}", self.source):
            self.reason = "java_unicode_preprocessing_not_modeled"
            return
        method = node if node.type == "method_declaration" else _ancestor(node, "method_declaration")
        if method is None or method.child_by_field_name("body") is None:
            self.reason = "complete_method_required"
            return
        self.method_node = method
        self._root_node = root
        parameters = method.child_by_field_name("parameters")
        method_name = method.child_by_field_name("name")
        if parameters is None or method_name is None or method.child_by_field_name("type") is None:
            self.reason = "missing_method_signature"
            return
        owners = []
        parent = method.parent
        if parent is None or parent.type not in {"class_body", "interface_body", "enum_body"}:
            self.reason = "named_class_method_required"
            return
        while parent is not None and parent.type != "program":
            if parent.type in {"class_declaration", "interface_declaration", "enum_declaration"}:
                name = parent.child_by_field_name("name")
                if name is None:
                    self.reason = "named_class_method_required"
                    return
                owners.append(self._text(name))
            elif parent.type not in {"class_body", "interface_body", "enum_body", "enum_body_declarations"}:
                self.reason = "local_or_anonymous_class_not_modeled"
                return
            parent = parent.parent
        if not owners:
            self.reason = "named_class_method_required"
            return
        package = ""
        for child in root.named_children:
            if child.type == "package_declaration":
                names = [n for n in child.named_children if n.type in {"identifier", "scoped_identifier"}]
                if len(names) != 1:
                    self.reason = "ambiguous_package_declaration"
                    return
                package = _compact(self._text(names[0])) + "."
        parameter_types = []
        for ordinal, parameter in enumerate(parameters.named_children):
            if parameter.type not in {"formal_parameter", "spread_parameter"}:
                self.reason = "unsupported_parameter_signature"
                return
            type_node = parameter.child_by_field_name("type")
            name_node = parameter.child_by_field_name("name")
            if parameter.type == "spread_parameter":
                # Varargs carry no ordinary integral binding. Preserve their
                # signature when the parser exposes enough information.
                declarators = [n for n in parameter.named_children if n.type == "variable_declarator"]
                if name_node is None and len(declarators) == 1:
                    name_node = declarators[0].child_by_field_name("name")
                if type_node is None:
                    type_node = next((n for n in parameter.named_children if n.type.endswith("_type") or n.type == "type_identifier"), None)
            if type_node is None or name_node is None:
                self.reason = "missing_parameter_type_or_name"
                return
            dimensions = parameter.child_by_field_name("dimensions")
            declared = _compact(self._text(type_node))
            if dimensions is not None:
                declared += _compact(self._text(dimensions))
            if parameter.type == "spread_parameter":
                declared += "..."
            parameter_types.append(declared)
            name = self._text(name_node)
            self._parameters.setdefault(name, []).append((parameter, declared, ordinal))
        owner = package + ".".join(reversed(owners))
        self._package_name = package.rstrip(".")
        self._owner_names = list(reversed(owners))
        self._parameter_types = parameter_types
        self._method_name = self._text(method_name)
        self.method_key = owner + "#" + self._text(method_name) + "(" + ",".join(parameter_types) + ")"
        self.signature_key = self.method_key
        for current in _walk(method):
            if current.type in {"formal_parameter", "catch_formal_parameter", "variable_declarator"}:
                name = current.child_by_field_name("name")
                if name is not None:
                    self._declarations.setdefault(self._text(name), []).append(current)
            elif current.type == "enhanced_for_statement":
                name = current.child_by_field_name("name")
                if name is not None:
                    self._declarations.setdefault(self._text(name), []).append(current)
            elif current.type == "lambda_expression":
                params = current.child_by_field_name("parameters")
                if params is not None:
                    for name in _walk(params):
                        if name.type == "identifier":
                            self._declarations.setdefault(self._text(name), []).append(name)
            if current.type in {"assignment_expression", "update_expression"}:
                target = current.child_by_field_name("left") if current.type == "assignment_expression" else current
                if target is not None:
                    self._writes.update(self._text(n) for n in _walk(target) if n.type == "identifier")

    def resolve(self, identifier_node):
        return self._resolve(identifier_node, frozenset())

    def match_graph_method(self, graph_node: dict) -> tuple[bool, str]:
        """Require the CFG owner, method name and complete JVM descriptor.

        No line-distance or method-name-only fallback is allowed. An unresolved
        reference type, generic signature, ambiguous import or member type keeps
        the comparison unsupported, even if a nearby method looks plausible.
        """
        if self.reason or self.method_node is None:
            return False, self.reason or "complete_method_required"
        method_id = graph_node.get("method_id") if isinstance(graph_node, dict) else None
        if not isinstance(method_id, str):
            return False, "missing_graph_method_id"
        if "#<init>(" in method_id or "#<clinit>(" in method_id:
            return False, "constructor_or_initializer_graph_method"
        match = re.fullmatch(r"(?P<owner>" + _IDENTIFIER + r"(?:\." + _IDENTIFIER
                             + r")*)#(?P<name>" + _IDENTIFIER + r")(?P<descriptor>\(.*\).+)", method_id)
        if match is None:
            return False, "malformed_graph_method_id"
        owner = ((self._package_name + ".") if self._package_name else "") + "$".join(self._owner_names)
        if match["owner"] != owner:
            return False, "graph_method_owner_mismatch"
        if match["name"] != self._method_name:
            return False, "graph_method_name_mismatch"
        scope = self.method_node
        while scope is not None and scope.type != "program":
            if scope.child_by_field_name("type_parameters") is not None:
                return False, "generic_method_or_owner_not_resolved"
            scope = scope.parent
        # Java cannot overload only by return type. Do not choose one of two
        # duplicate source declarations merely because one descriptor matches.
        for sibling in self.method_node.parent.named_children:
            if sibling.type == "method_declaration" and not _same_node(sibling, self.method_node):
                other = ModelContext.from_node(sibling, self.source)
                if not other.reason and other.method_key == self.method_key:
                    return False, "duplicate_source_method_signature"
        imports, wildcard_imports, import_reason = self._signature_imports()
        if import_reason:
            return False, import_reason
        parameters = []
        for declared in self._parameter_types:
            descriptor, reason = self._type_descriptor(declared, imports, wildcard_imports, allow_void=False)
            if reason:
                return False, "parameter_" + reason
            parameters.append(descriptor)
        return_type = self.method_node.child_by_field_name("type")
        if return_type is None:
            return False, "missing_return_type"
        declared_return = _compact(self._text(return_type))
        dimensions = self.method_node.child_by_field_name("dimensions")
        if dimensions is not None:
            declared_return += _compact(self._text(dimensions))
        returned, reason = self._type_descriptor(declared_return, imports, wildcard_imports, allow_void=True)
        if reason:
            return False, "return_" + reason
        expected = "(" + "".join(parameters) + ")" + returned
        if match["descriptor"] != expected:
            return False, "graph_method_descriptor_mismatch"
        return True, ""

    def _signature_imports(self):
        imports, wildcards = {}, []
        for node in self._root_node.named_children:
            if node.type != "import_declaration":
                continue
            text = self._text(node).strip()
            parsed = re.fullmatch(r"import\s+(?P<static>static\s+)?(?P<name>" + _IDENTIFIER
                                  + r"(?:\s*\.\s*" + _IDENTIFIER + r")*)(?P<wild>\s*\.\s*\*)?\s*;", text)
            if parsed is None:
                return {}, [], "unresolved_import_syntax"
            name = _compact(parsed["name"])
            if parsed["static"]:
                # A static import may import an inherited/member type. Without
                # the declaring class's source it is not a simple type import.
                wildcards.append(name)
            elif parsed["wild"]:
                if name != "java.lang":
                    wildcards.append(name)
            else:
                imports.setdefault(name.rsplit(".", 1)[-1], set()).add(name)
        return imports, wildcards, ""

    def _type_descriptor(self, declared, imports, wildcard_imports, *, allow_void):
        dimensions = 0
        if declared.endswith("..."):
            dimensions += 1
            declared = declared[:-3]
        while declared.endswith("[]"):
            dimensions += 1
            declared = declared[:-2]
        if dimensions > 255:
            return None, "array_dimension_limit"
        if declared in _JVM_PRIMITIVES:
            if declared == "void" and (not allow_void or dimensions):
                return None, "invalid_void_type"
            return "[" * dimensions + _JVM_PRIMITIVES[declared], ""
        if not re.fullmatch(_IDENTIFIER + r"(?:\." + _IDENTIFIER + r")*", declared):
            return None, "generic_or_unresolved_reference_type"
        known, ambiguous = self._source_type_names()
        if "." in declared:
            if declared in ambiguous:
                return None, "ambiguous_source_type_name"
            first = declared.split(".", 1)[0]
            roots = {binary for key, binary in known.items() if key.rsplit(".", 1)[-1] == first}
            if first in imports or any(key.rsplit(".", 1)[-1] == first for key in ambiguous):
                return None, "qualified_type_root_is_shadowed"
            if roots and not (
                len(roots) == 1 and declared in known
                and known[declared].startswith(next(iter(roots)) + "$")
            ):
                return None, "qualified_type_root_is_shadowed"
            if declared in known:
                binary_name = known[declared]
            else:
                # A qualified name alone does not say which dots denote packages
                # and which denote nested classes. Only this explicitly known
                # Java namespace is accepted without another class's source.
                if first in imports or any(key.rsplit(".", 1)[-1] == first for key in known):
                    return None, "qualified_type_root_is_shadowed"
                if declared.startswith("java.lang.") and declared[len("java.lang."):] in _JAVA_LANG_TYPES:
                    binary_name = declared.replace(".", "/")
                else:
                    return None, "external_reference_binary_name_not_verified"
        else:
            scope = self.method_node.parent
            while scope is not None and scope.type != "program":
                if any(scope.child_by_field_name(field) is not None for field in ("superclass", "interfaces")):
                    return None, "inherited_member_type_not_resolved"
                scope = scope.parent
            imported = imports.get(declared, set())
            source_matches = {key: value for key, value in known.items() if key.rsplit(".", 1)[-1] == declared}
            if len(imported) > 1 or any(key.rsplit(".", 1)[-1] == declared for key in ambiguous):
                return None, "ambiguous_short_type_name"
            if source_matches:
                # A source-local declaration might shadow java.lang or an
                # import, including through a nested scope. Reject uncertainty.
                if len(source_matches) != 1 or imported:
                    return None, "ambiguous_short_type_name"
                key, binary_name = next(iter(source_matches.items()))
                top_level = self._package_name + "." + declared if self._package_name else declared
                owner_prefixes = [((self._package_name + ".") if self._package_name else "")
                                  + ".".join(self._owner_names[:i]) for i in range(1, len(self._owner_names) + 1)]
                if key != top_level and key not in owner_prefixes:
                    return None, "source_member_type_scope_not_resolved"
            elif imported:
                imported_name = next(iter(imported))
                if imported_name in known:
                    binary_name = known[imported_name]
                elif imported_name.startswith("java.lang.") and imported_name[len("java.lang."):] in _JAVA_LANG_TYPES:
                    binary_name = imported_name.replace(".", "/")
                else:
                    return None, "external_reference_binary_name_not_verified"
            elif wildcard_imports:
                return None, "wildcard_import_cannot_resolve_short_type"
            elif declared in _JAVA_LANG_TYPES:
                binary_name = "java/lang/" + declared
            else:
                return None, "short_reference_type_not_resolved"
        return "[" * dimensions + "L" + binary_name + ";", ""

    def _source_type_names(self):
        known, ambiguous = {}, set()
        for node in _walk(self._root_node):
            if node.type not in {"class_declaration", "interface_declaration", "enum_declaration"}:
                continue
            owners, current, valid = [], node, True
            while current is not None and current.type != "program":
                if current.type in {"class_declaration", "interface_declaration", "enum_declaration"}:
                    name = current.child_by_field_name("name")
                    if name is None:
                        valid = False
                        break
                    owners.append(self._text(name))
                elif current.type not in {"class_body", "interface_body", "enum_body", "enum_body_declarations"}:
                    valid = False
                    break
                current = current.parent
            if not valid:
                continue
            owners.reverse()
            canonical = ((self._package_name + ".") if self._package_name else "") + ".".join(owners)
            binary = ((self._package_name.replace(".", "/") + "/") if self._package_name else "") + "$".join(owners)
            if canonical in known:
                ambiguous.add(canonical)
            known[canonical] = binary
        return known, ambiguous

    def _reject(self, reason, node=None, java_type=None):
        return Binding(java_type=java_type, reason=reason, evidence={
            "source_sha256": self.source_sha256, "source_path": self.source_path,
            "method_key": self.method_key, "use_start_byte": getattr(node, "start_byte", None),
            "use_end_byte": getattr(node, "end_byte", None), "rejection": reason,
        })

    def _evidence(self, declaration, use, java_type):
        return {"source_sha256": self.source_sha256, "source_path": self.source_path,
                "method_key": self.method_key, "java_type": java_type,
                "declaration_start_byte": declaration.start_byte,
                "declaration_end_byte": declaration.end_byte,
                "declaration_text": self._text(declaration),
                "use_start_byte": use.start_byte, "use_end_byte": use.end_byte}

    def _resolve(self, node, seen):
        if self.reason:
            return self._reject(self.reason, node)
        if node is None or node.type != "identifier":
            return self._reject("identifier_node_required", node)
        if not _same_node(_ancestor(node, "method_declaration"), self.method_node):
            return self._reject("identifier_from_other_method", node)
        enclosing = node.parent
        while enclosing is not None and not _same_node(enclosing, self.method_node):
            if enclosing.type in {"lambda_expression", "class_body", "class_declaration",
                                   "interface_declaration", "enum_declaration"}:
                return self._reject("identifier_in_nested_executable_scope", node)
            enclosing = enclosing.parent
        # The node must be a value use, not the name after an object dot or the
        # name of a declaration/call. Parenthesized and arithmetic uses are fine.
        if node.parent is not None and node.parent.type in {
            "field_access", "method_invocation", "method_reference", "variable_declarator",
            "formal_parameter", "catch_formal_parameter", "class_declaration",
        }:
            if not (node.parent.type == "variable_declarator" and _same_node(node.parent.child_by_field_name("value"), node)):
                return self._reject("identifier_is_not_a_simple_value_use", node)
        name = self._text(node)
        if name in seen or len(seen) >= 64:
            return self._reject("cyclic_or_deep_local_initializer", node)
        declarations = self._declarations.get(name, [])
        if len(declarations) != 1:
            return self._reject("ambiguous_or_missing_declaration", node)
        if name in self._writes:
            return self._reject("binding_written_in_method", node)
        if name in self._parameters:
            candidates = self._parameters[name]
            if len(candidates) != 1:
                return self._reject("ambiguous_parameter_declaration", node)
            declaration, java_type, ordinal = candidates[0]
            if java_type not in INTEGRAL_TYPES:
                return self._reject("parameter_is_not_scalar_integral", node, java_type)
            evidence = self._evidence(declaration, node, java_type)
            evidence.update({"binding_kind": "parameter", "parameter_ordinal": ordinal})
            return Binding(java_type=java_type, parameter_key=self.method_key + ":parameter:" + str(ordinal), evidence=evidence)
        declarator = declarations[0]
        declaration = declarator.parent
        if (declarator.type != "variable_declarator" or declaration is None
                or declaration.type != "local_variable_declaration"
                or declaration.parent is None or declaration.parent.type != "block"):
            return self._reject("local_requires_direct_block_declaration", node)
        if len([n for n in declaration.named_children if n.type == "variable_declarator"]) != 1:
            return self._reject("multiple_declarators_not_modeled", node)
        body = self.method_node.child_by_field_name("body")
        if not _same_node(declaration.parent, body):
            return self._reject("local_not_in_method_entry_block", node)
        for preceding in body.named_children:
            if _same_node(preceding, declaration):
                break
            if preceding.type not in {"local_variable_declaration", "line_comment", "block_comment"}:
                return self._reject("local_after_method_entry_declaration_prefix", node)
        if not _same_node(_ancestor(node, "block"), declaration.parent):
            return self._reject("local_use_in_different_block", node)
        if declaration.end_byte > node.start_byte:
            return self._reject("local_declaration_not_before_use", node)
        type_node = declaration.child_by_field_name("type")
        dimensions = declarator.child_by_field_name("dimensions")
        java_type = self._text(type_node).strip() if type_node is not None else None
        if java_type not in INTEGRAL_TYPES or dimensions is not None:
            return self._reject("local_is_not_scalar_integral", node, java_type)
        initializer = declarator.child_by_field_name("value")
        if initializer is None:
            return self._reject("local_has_no_initializer", node, java_type)
        inferred, reason = self._expression_type(initializer, seen | {name})
        if reason:
            return self._reject("initializer_" + reason, node, java_type)
        widening = {"byte": {"short", "int", "long"}, "short": {"int", "long"},
                    "char": {"int", "long"}, "int": {"long"}, "long": set()}
        if inferred != java_type and java_type not in widening.get(inferred, set()):
            from .integrals import evaluate_integral_constant
            constant = evaluate_integral_constant(initializer, self.source)
            limits = {"byte": (-128, 127), "short": (-32768, 32767), "char": (0, 65535)}
            if (constant is None or constant.java_type not in {"byte", "short", "char", "int"}
                    or java_type not in limits or not limits[java_type][0] <= constant.value <= limits[java_type][1]):
                return self._reject("initializer_assignment_conversion_not_supported", node, java_type)
        evidence = self._evidence(declaration, node, java_type)
        evidence.update({"binding_kind": "local", "initializer_start_byte": initializer.start_byte,
                         "initializer_end_byte": initializer.end_byte})
        return Binding(java_type=java_type, initializer_node=initializer, evidence=evidence)

    def _expression_type(self, node, seen):
        from .integrals import evaluate_integral_constant
        constant = evaluate_integral_constant(node, self.source)
        if constant is not None:
            return constant.java_type, ""
        if node.type == "identifier":
            binding = self._resolve(node, seen)
            return binding.java_type, binding.reason
        if node.type == "parenthesized_expression" and len(node.named_children) == 1:
            return self._expression_type(node.named_children[0], seen)
        if node.type == "cast_expression":
            target = node.child_by_field_name("type")
            value = node.child_by_field_name("value")
            java_type = self._text(target).strip() if target is not None else None
            if java_type not in INTEGRAL_TYPES or value is None:
                return None, "non_integral_cast"
            _, reason = self._expression_type(value, seen)
            return java_type, reason
        if node.type == "unary_expression":
            operand = node.child_by_field_name("operand")
            operator = node.child_by_field_name("operator")
            if operand is None or operator is None or self._text(operator) not in {"+", "-", "~"}:
                return None, "unsupported_unary_expression"
            operand_type, reason = self._expression_type(operand, seen)
            return ("long" if operand_type == "long" else "int"), reason
        if node.type == "binary_expression":
            operator = node.child_by_field_name("operator")
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            operation = self._text(operator) if operator is not None else None
            if left is None or right is None or operation not in {"+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>", ">>>"}:
                return None, "unsupported_binary_expression"
            lt, lr = self._expression_type(left, seen)
            rt, rr = self._expression_type(right, seen)
            if lr or rr:
                return None, lr or rr
            if operation in {"<<", ">>", ">>>"}:
                return ("long" if lt == "long" else "int"), ""
            return ("long" if "long" in {lt, rt} else "int"), ""
        return None, "unsupported_expression:" + node.type
