from __future__ import annotations

import sys
from functools import cache
from io import BytesIO
from typing import TYPE_CHECKING, Dict, Optional, cast

from tpk_ar import TpkFile, TpkTypeTreeBlob, TpkUnityClass, TpkUnityNode
from tpk_ar import UnityVersion as TpkUnityVersion

from . import TypeTreeHelper
from .UnityVersion import UnityVersion

if TYPE_CHECKING:
    from .TypeTreeHelper import TypeTreeNode


@cache
def get_typetree() -> TpkTypeTreeBlob:
    package = "UnityPy.resources"
    resource = "lzma.tpk"

    tpk_data: bytes
    if sys.version_info >= (3, 9):
        from importlib.resources import files

        tpk_data = files(package).joinpath(resource).read_bytes()

    else:
        from importlib.resources import open_binary

        tpk_data = open_binary(package, resource).read()

    with BytesIO(tpk_data) as stream:
        tree = TpkFile.parse(stream).GetDataBlob()
    assert isinstance(tree, TpkTypeTreeBlob)
    return tree


@cache
def get_typetree_node(class_id: int, version: UnityVersion):
    tpk_version = cast(TpkUnityVersion, version)
    class_info = get_typetree().ClassInformation[class_id].getVersionedClass(tpk_version)
    if class_info is None:
        raise ValueError("Could not find class info for class id {}".format(class_id))

    node = generate_node(class_info)
    return node


@cache
def generate_node(class_info: TpkUnityClass) -> "TypeTreeNode":
    assert class_info.ReleaseRootNode is not None, "Class {} has no ReleaseRootNode".format(class_info)

    TypeTreeNode = TypeTreeHelper.TypeTreeNode

    nodes = []
    NODES = get_typetree().NodeBuffer
    STRINGBUFFER = get_typetree().StringBuffer
    stack = [(class_info.ReleaseRootNode, 0)]
    index = 0
    while stack:
        node_id, level = stack.pop(0)
        node: TpkUnityNode = NODES[node_id]
        nodes.append(
            TypeTreeNode(
                m_ByteSize=node.ByteSize,
                m_Index=index,
                m_Version=node.Version,
                m_MetaFlag=node.MetaFlag,
                m_Level=level,
                m_Type=STRINGBUFFER[node.TypeName],
                m_Name=STRINGBUFFER[node.Name],
            )
        )
        stack = [(node_id, level + 1) for node_id in node.SubNodes] + stack
        index += 1
    return TypeTreeNode.from_list(nodes)


@cache
def get_common_strings(version: Optional[UnityVersion] = None) -> Dict[int, str]:
    tpk_version: TpkUnityVersion | None = cast(TpkUnityVersion, version) if version is not None else None
    try:
        return get_typetree().CommonString.BuildMap(get_typetree().StringBuffer, tpk_version)
    except Exception:  # u3d.py offline patch: tpk_ar unavailable -> built-in table
        return _FALLBACK_COMMON_STRINGS


# --- u3d.py offline patch: Unity's built-in common-strings table (offset -> string) ---
_CS = (
    "AABB\0AnimationClip\0AnimationCurve\0AnimationState\0Array\0Base\0BitField\0"
    "bitset\0bool\0char\0ColorRGBA\0Component\0data\0deque\0double\0dynamic_array\0"
    "FastPropertyName\0first\0float\0Font\0GameObject\0Generic Mono\0GradientNEW\0"
    "GUID\0GUIStyle\0int\0list\0long long\0map\0Matrix4x4f\0MdFour\0MonoBehaviour\0"
    "MonoScript\0m_ByteSize\0m_Curve\0m_EditorClassIdentifier\0m_EditorHideFlags\0"
    "m_Enabled\0m_ExtensionPtr\0m_GameObject\0m_Index\0m_IsArray\0m_IsStatic\0"
    "m_MetaFlag\0m_Name\0m_ObjectHideFlags\0m_PrefabInternal\0m_PrefabParentObject\0"
    "m_Script\0m_StaticEditorFlags\0m_Type\0m_Version\0Object\0pair\0"
    "PPtr<Component>\0PPtr<GameObject>\0PPtr<Material>\0PPtr<MonoBehaviour>\0"
    "PPtr<MonoScript>\0PPtr<Object>\0PPtr<Prefab>\0PPtr<Sprite>\0PPtr<TextAsset>\0"
    "PPtr<Texture>\0PPtr<Texture2D>\0PPtr<Transform>\0Prefab\0Quaternionf\0Rectf\0"
    "RectInt\0RectOffset\0second\0set\0short\0size\0SInt16\0SInt32\0SInt64\0SInt8\0"
    "staticvector\0string\0TextAsset\0TextMesh\0Texture\0Texture2D\0Transform\0"
    "TypelessData\0UInt16\0UInt32\0UInt64\0UInt8\0unsigned int\0unsigned long long\0"
    "unsigned short\0vector\0Vector2f\0Vector3f\0Vector4f\0m_ScriptingClassName\0"
)
_FALLBACK_COMMON_STRINGS: Dict[int, str] = {}
_o = 0
for _s in _CS.split("\0")[:-1]:
    _FALLBACK_COMMON_STRINGS[_o] = _s
    _o += len(_s) + 1
