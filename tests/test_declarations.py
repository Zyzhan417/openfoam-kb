"""005 T001/T004: the namespace-scope alias extractor, one assertion per criterion.

Assertions are read off `specs/005-typedef-symbols/tasks.md` T001 (six criteria) and
spec FR-004 / Edge Cases — not off the implementation. Each criterion gets its own test so
a rule that stops working shows up as one named failure, not as "the extractor test is red".
"""

from foamkb.declarations import namespace_scope_aliases

# Shaped like volFieldsFwd.H: the typedefs are *not* the block head, and one block declares
# several aliases (measured: up to 7 in the corpus).
FWD = """template<class Type>
class VolField;

typedef VolInternalField<scalar> volScalarField;
typedef VolField<vector> volVectorField;
using volSymmTensorField = VolField<symmTensor>;
"""

# A class member alias is what 1,144 indented lines would have added: `type`, `alphaField`,
# `rhoField` — generic names that would let any single class hijack a query (spec ①).
MEMBER = """class wallFn
{
public:
    typedef volScalarField alphaField;
    using rhoField = volScalarField;
};
"""


# --- ② the two forms, and the alias that is not the block head -----------------

def test_a_typedef_line_yields_the_declared_name_and_its_kind():
    """T001②: `typedef <type> <name>;` -> (name, "typedef"), head line irrelevant."""
    assert namespace_scope_aliases(FWD) == [("volScalarField", "typedef"),
                                            ("volVectorField", "typedef"),
                                            ("volSymmTensorField", "using")]


def test_a_single_line_using_alias_yields_kind_using():
    assert namespace_scope_aliases("using VectorField = Field<vector>;\n") == \
        [("VectorField", "using")]


def test_a_multi_line_using_alias_takes_the_name_before_the_equals():
    """T001②/Edge Case: `using X =` continued on the next line still declares `X`."""
    text = ("using IsothermalThermophysicalTransportPhaseModel =\n"
            "    ThermophysicalTransportModel<phaseModel>;\n")
    assert namespace_scope_aliases(text) == [("IsothermalThermophysicalTransportPhaseModel",
                                              "using")]


def test_a_template_prefixed_alias_is_read_from_its_using_line():
    assert namespace_scope_aliases("template<class Type>\nusing TypeField = Field<Type>;\n") == \
        [("TypeField", "using")]


def test_a_using_directive_declares_no_name():
    assert namespace_scope_aliases("using namespace Foam;\n") == []


# --- ① column 0 only ----------------------------------------------------------

def test_member_aliases_in_a_class_body_are_not_namespace_scope():
    """T001①: indentation is the whole rule — these are class members, not type names."""
    assert namespace_scope_aliases(MEMBER) == []


def test_one_leading_space_is_enough_to_exclude_a_line():
    """T001①: '缩进 1 个空格即不收' — the boundary is column 0, not 'mostly unindented'."""
    assert namespace_scope_aliases(" typedef volScalarField alphaField;\n") == []


def test_eight_space_indentation_is_excluded_too():
    """The corpus has 299 aliases at indent 8; none of them may become a symbol name."""
    assert namespace_scope_aliases("        typedef scalar cmptType;\n") == []


# --- ③ function pointers and macro-typed declarations -------------------------

def test_function_pointer_typedefs_are_not_extracted():
    """T001③/Edge Case: the name sits inside parentheses (`(*IstreamManip)`)."""
    text = ("typedef void (*IstreamManip)(std::istream&, int&);\n"
            "typedef Istream& (*IOstreamManip)(Istream&);\n"
            "typedef void (*OstreamManip)(std::ostream&, const int);\n")
    assert namespace_scope_aliases(text) == []


def test_a_macro_typed_typedef_is_not_extracted():
    """FR-004's parenthesis test also drops `INT_SIZE(int, _t) label;`.

    Measured consequence: 214 alias-shaped column-0 lines in the build corpus minus these
    two (`label`, `uLabel`) is exactly the 212 the spec froze. Both are macro-generated
    types, i.e. the same "the name is not the last identifier outside a call" shape.
    """
    text = ("typedef INT_SIZE(int, _t) label;\n"
            "typedef UINT_SIZE(uint, _t) uLabel;\n")
    assert namespace_scope_aliases(text) == []


# --- ④ SYMBOL_KEYWORDS -------------------------------------------------------

def test_a_name_in_symbol_keywords_is_not_a_declaration():
    """T001④: without this guard a `typedef int for;` becomes a 'symbol' for `for`."""
    assert namespace_scope_aliases("typedef int for;\n") == []
    assert namespace_scope_aliases("using if = int;\n") == []


def test_a_keyword_guard_does_not_eat_its_neighbours():
    text = "typedef int for;\ntypedef int iterationCount;\n"
    assert namespace_scope_aliases(text) == [("iterationCount", "typedef")]


# --- ⑤ order, and one row per name --------------------------------------------

def test_aliases_come_back_in_source_order():
    text = ("typedef Field<scalar> ScalarField;\n"
            "using VectorField = Field<vector>;\n"
            "typedef Field<tensor> TensorField;\n")
    assert [name for name, _ in namespace_scope_aliases(text)] == \
        ["ScalarField", "VectorField", "TensorField"]


def test_a_repeated_name_keeps_only_its_first_declaration():
    """T001⑤: one name must not become two rows for the same chunk."""
    text = ("typedef Field<scalar> ScalarField;\n"
            "using ScalarField = Field<scalar>;\n"
            "typedef Field<scalar> ScalarField;\n")
    assert namespace_scope_aliases(text) == [("ScalarField", "typedef")]


# --- ⑥ one line at a time ----------------------------------------------------

def test_a_declaration_split_over_two_lines_yields_nothing():
    """T001⑥: no cross-line matching — half a declaration is not a name."""
    assert namespace_scope_aliases("typedef VolField<scalar>\n    volScalarField;\n") == []


def test_a_typedef_line_inside_a_block_comment_is_collected():
    """T001⑥, the known boundary, pinned rather than hoped for.

    The scan is line-anchored and does not carry block-comment state (AGENTS D15 forbids
    reading comment state off a single line, so the choice is "state machine or nothing";
    a state machine was measured to buy nothing). Measured on the build corpus: **0 of the
    214 alias-shaped column-0 lines sit inside a block comment**, so today this collects no
    false name. The assertion below is what "the extractor is line-anchored" means, and it
    is what to revisit if that measurement ever stops holding.
    """
    text = "/*\ntypedef VolField<scalar> volScalarField;\n*/\n"
    assert namespace_scope_aliases(text) == [("volScalarField", "typedef")]


# --- degenerate input --------------------------------------------------------

def test_an_empty_body_has_no_aliases():
    assert namespace_scope_aliases("") == []
    assert namespace_scope_aliases("int x = 3;\n") == []
