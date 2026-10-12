# UnityPy glTF 模型材质绑定与 Blender 验证

## 适用症状

UnityPy 导出的 glTF 在 Blender 中能看到几何体、UV 和索引，模型目录里也复制了若干候选贴图，但材质仍显示为纯色或无贴图。这不是 Blender 导入失败，而是 glTF 没有声明材质贴图绑定。

## 根因

外部复制 `textures/` 文件夹本身不足以让 Blender 识别材质。glTF JSON 必须同时写入：

- `images[*].uri`，指向模型目录内的相对贴图路径。
- `textures[*].source`，引用对应 image。
- `materials[*].pbrMetallicRoughness.baseColorTexture`，引用对应 texture。
- `meshes[*].primitives[*].material`，把 primitive 指向该 material。

如果只导出 geometry/UV/index，或只把贴图复制到旁边目录，Blender 无法推断哪张图片应该接到 Base Color。

## 必要提取链

Unity 模型贴图绑定应优先从真实 Renderer 材质关系恢复，而不是只按文件名猜测：

```text
SkinnedMeshRenderer / MeshRenderer
  -> (SkinnedMeshRenderer.m_Mesh 或 MeshRenderer.m_GameObject -> MeshFilter.m_Mesh)
  -> m_Materials[]
  -> Material
  -> m_SavedProperties.m_TexEnvs["_MainTex"]
  -> Texture2D
  -> Texture2D.m_Name
```

`scripts/export_unitypy_meshes_to_gltf.py` 的 `build_mesh_texture_binding_map(env)` 应在每个 Unity 容器内建立 Mesh 到 Renderer/Material/Texture 的证据映射。MeshRenderer 本身通常没有 `m_Mesh`，必须通过同 GameObject 的 MeshFilter 拼接。Material 或 Texture PPtr 指向外部 serialized file 时，可用 `--expected-index` 指向已完成的 UnityPy `all_resources_index.csv`，从全量 Material metadata 与唯一 Texture2D path id 名称补全证据；重复 path id 或多 Renderer/Material 绑定必须保留 binding_count/ambiguous，不得静默称为唯一原材质。

## 导出行为

运行 UnityPy Mesh glTF 导出时，放在 UnityPy 可读资源导出之后、最终模型验收之前：

```powershell
& $py <skill>\scripts\export_unitypy_meshes_to_gltf.py `
  --source "<game-root>" `
  --study-root "<study-root>" `
  --expected-index "<study-root>\0.原始导出\UnityPy\<输出>\all_resources_index.csv"
```

导出结果必须满足：

- 每个模型目录包含 `.gltf` 和对应 `.bin`。
- 真实绑定贴图复制到模型局部 `textures/<Texture2D名称>.png`。
- glTF 使用相对 URI，例如 `textures/hero_diffuse.png`，不得写绝对路径。
- `obj_to_gltf(..., material_texture_uri=...)` 写入 `images`、`textures`、`materials`、`baseColorTexture`，并把 primitive 的 `material` 设为 `0`。
- Unity VFX 薄片、Plane 和 Trail 形状可能只从背面进入固定预览相机；学习用 glTF 材质写入 `doubleSided=true`，避免合法几何被离线缩略图误显示为空白。该标志只服务预览，不代表原 Shader 的 Cull 配置已经恢复。
- 模型索引包含 `material_texture_name`、`material_texture_uri`、`material_binding_source`，便于报告和抽样验证。
- 专项/并行分支使用 `--model-output-subdir <安全相对子根>` 与 `--index-subdir <安全相对子根>`，不得覆盖既有模型分支；两次相同运行必须复用相同内容并保留 mtime。

当 `material_binding_source` 为 `unity_material_main_texture` 时，表示贴图来自 Renderer -> Material -> `_MainTex` 的真实绑定链，应优先视为可用材质。

## 回退与标记

如果无法从 `_MainTex` 恢复绑定，但存在同名前缀贴图候选，可以使用保守回退：

- 把候选贴图复制到 `textures/待验证_同名前缀/`。
- 选择一个同名前缀候选写入 glTF 的 `baseColorTexture`，让 Blender 能直接预览。
- 在索引中把 `material_binding_source` 标为 `same_name_candidate`。
- 保留 `material_texture_uri`，路径通常类似 `textures/待验证_同名前缀/<候选贴图>.png`。

`same_name_candidate` 只是可视化候选，不等同于已验证材质。报告中应把这类模型标为待复核，避免把噪声图、遮罩图、法线图或 UI 小图误当 Base Color。

如果没有真实绑定也没有候选贴图，保持 `material_texture_name`、`material_texture_uri`、`material_binding_source` 为空，并在摘要中统计为未绑定；不要伪造贴图。

## Blender CLI 验证

完成导出后，用 Blender 后台脚本抽样验证，而不是只检查 glTF 文件存在。示例流程：

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" `
  --background `
  --python "<study-root>\4.临时目录\工具日志\Blender验证\check_gltf_materials_in_blender.py"
```

验证脚本应至少检查：

- 能导入样本 `.gltf`，且对象有 mesh 数据。
- 材质启用节点，并存在 Image Texture 节点。
- Image Texture 的 Color 输出连接到 Principled BSDF 的 Base Color。
- Image Texture 节点引用的图片路径存在，且 Blender 没有报告 missing image。
- VFX 薄片或 Plane 样本从固定预览相机可见；若单面/双面 A/B 只有双面可见，应保留 `doubleSided=true` 并记录为预览兼容处理。
- 对 `unity_material_main_texture` 与 `same_name_candidate` 分别抽样，报告各自通过/失败数量。

验收不能只写“Blender 能导入”。必须明确“Blender 可见材质贴图”或“glTF 中存在 `baseColorTexture` 且 Blender 中 Image Texture 已连接 Base Color”。

## 验收标准

- Unity full/art 导出中，发现 UnityPy Mesh 时应运行 `export_unitypy_meshes_to_gltf.py`，或在报告中写明为何不适用。
- 模型目录中的 glTF 至少对可恢复贴图的模型写入 `images/textures/materials/baseColorTexture`。
- 模型局部贴图路径使用相对 URI，文件实际存在。
- 索引统计包含 `unity_material_main_texture`、`same_name_candidate` 和无绑定数量。
- Blender CLI 抽样验证记录在 `4.临时目录/工具日志/Blender验证/`，并在 `2.报告/00_研究总览.md` 摘要说明。
- `1.分类资源/模型/` 中标为可交付的模型必须能在 Blender 中看到网格和材质贴图；仅同名前缀候选需标注待复核。

## 残余风险

- 同一个 Mesh 可能被多个 Renderer 复用，并挂不同 Material 或不同 `_MainTex`；单个 `Mesh.path_id -> Texture2D` 映射可能只能保留一个绑定。遇到明显多绑定时，在索引或报告中标记需要按 Renderer 实例拆分验证。
- 部分 Material 没有 `_MainTex`，可能使用 `_BaseMap`、ShaderGraph 自定义属性、运行时替换、顶点色或程序化材质；不要把它误判为导出失败。
- 有些模型本来就是无贴图、纯色材质、碰撞/占位/特效形状载体；应作为真实无贴图模型记录，而不是强行绑定候选图片。
- 同名前缀候选可能只适合作预览，不能替代真实 Unity 材质链证据。
