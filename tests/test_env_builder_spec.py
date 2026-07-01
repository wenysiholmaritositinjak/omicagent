from omicagent.env_builder import EnvBuilder


def test_build_spec_scanpy():
    spec = EnvBuilder()._build_spec(["scanpy"])

    assert spec.env_name == "scagent"
    assert spec.language == "python"
    assert "scanpy" in spec.analysis_tools
    assert "import scanpy" in spec.verify_cmds
    assert spec.selection_reason


def test_build_spec_seurat():
    spec = EnvBuilder()._build_spec(["seurat"])

    assert spec.env_name == "seurat"
    assert spec.language == "r"
    assert "seurat" in spec.analysis_tools
    assert "library(Seurat)" in spec.verify_cmds
    assert spec.selection_reason


def test_build_spec_scanpy_saturn():
    spec = EnvBuilder()._build_spec(["scanpy", "saturn"])

    assert spec.env_name == "scagent"
    assert spec.language == "python"
    assert "scanpy" in spec.analysis_tools
    assert "saturn" in spec.analysis_tools
    assert "import torch" in spec.verify_cmds
    assert "import scvi" in spec.verify_optional
    assert spec.selection_reason


def test_build_spec_samap():
    spec = EnvBuilder()._build_spec(["samap"])

    assert spec.env_name == "samap"
    assert spec.language == "python"
    assert "samap" in spec.analysis_tools
    assert "from samap import SAMAP" in spec.verify_cmds
    assert spec.selection_reason
