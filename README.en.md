# Open Photo Workflow

[中文](README.md) · English

A shared Agent-assisted photo workflow, and a practical way to put your ChatGPT Pro `image_gen` allowance to work.

The main contribution is the method: how to review a collection, choose an input, describe a photographic look, and decide between local grading and image generation. Adapt it to your needs, and share useful prompts and lessons from failed edits.

**The code is for reference only. Installing the skills requires no Python dependencies or test runs.** [reference-code/](reference-code/) contains local implementations to read, modify, or adapt with an Agent when needed. No photographs or model weights are included.

## Using your Pro allowance

Sign into Codex with ChatGPT Pro and let the Agent call the host's built-in `image_gen` tool. This route needs no OpenAI API key. Generation uses the subscription's shared usage allowance; availability and limits depend on the account. See the [official documentation](https://learn.chatgpt.com/docs/pricing).

Skills guide the Agent; they do not supply an image service or increase your allowance. Model names, token usage, and native resolution are not guaranteed when the tool does not expose them. Other Agents can use the method with their own available image tools.

## The workflow

1. **Review the collection.** Choose a look based on subject matter, lighting, and intended use. Portraits and still life may need different approaches. Group matching RAW/JPEG pairs.
2. **Prepare the input.** Prefer a usable matching camera JPEG, then an embedded RAW JPEG. Both need a native long edge of at least 3840 pixels; preserve the aspect ratio. Develop the RAW when neither qualifies or when exposure recovery is needed.
3. **Choose a method for the goal.** Use local tools for light and color. Call `image_gen` for portrait retouching, background cleanup, or generative styling. Face/body changes and object removal follow the user's request.
4. **Calibrate when useful.** Familiar small batches can proceed directly; try a representative image for a new look. Usually generate one version per photo and retry only for a specific problem. Reuse approved samples.
5. **Review and deliver.** Apply super-resolution and light sharpening when useful, keeping native results. Review at the agreed viewing size. Minor defects may be acceptable; obvious identity, anatomy, and important text errors need attention. Deliver JPEGs, brief records, and a static comparison gallery.

The LLM handles photographic decisions and exceptions. Automate local steps where practical, without repeated hashing, gallery refresh loops, or unnecessary intermediate versions. The [photographic goals and prompts](skills/photo-workflow/references/photographic-goals.md) provide examples in Chinese.

## Two skills

| Skill | Purpose |
| --- | --- |
| [photo-workflow](skills/photo-workflow/SKILL.md) | Collection-level style, input preparation, method selection, review, and delivery |
| [imagegen-tiled-upscale](skills/imagegen-tiled-upscale/SKILL.md) | Optional four-tile generative detail enhancement, alignment, blending, and super-resolution experiments |

Tiling needs at least four image calls and can introduce seams or distortion, so it is not the default. It improves perceived detail; generated letters and facial textures are not evidence of recovered information.

## Ask an Agent to install it

Copy this prompt into an Agent with local file access:

```text
Install both skills from https://github.com/hangxu/open-photo-workflow.
Read README.en.md and INSTALL.md first, then install skills/photo-workflow and
skills/imagegen-tiled-upscale into this Agent's skill directory.
Codex uses the skills/ directory under CODEX_HOME when set, otherwise ~/.codex/skills/.
Explain differences before replacing any existing skill with the same name.
Install skills only: no Python dependencies, test runs, photo processing/uploads, or image-generation calls.
Check whether this session has built-in image_gen. If not, explain the limitation; do not switch to a paid API or ask for an API key.
Report the skill locations and how to invoke them. Keep reference-code/ optional; prepare local tools only when an actual editing task needs them.
```

See [INSTALL.md](INSTALL.md) for manual installation. The README and installation guide are bilingual; skill instructions are currently mainly in Chinese.

After installation, you can ask:

```text
Use $photo-workflow on the photo directory I specify and save into a new directory.
Review the collection and plan a bright, natural travel-photography look, keeping people recognizable.
Follow my requested retouching strength and background removals. Do not generate when local grading is enough.
Minor defects that do not distract at normal viewing size are acceptable. Deliver JPEGs and a static comparison gallery.
```

## Limits and contributions

AI may change identity, clouds, background texture, and small text. Prompts can reduce drift but cannot eliminate it. Upscaled dimensions are not native model resolution. Keep originals separately; calling the image tool sends selected inputs to the service provider.

Issues and PRs are welcome for text-based experience, sanitized parameters, and workflow improvements. Do not submit photographs, previews, screenshots, generated images, or images encoded in text. See [CONTRIBUTING.md](CONTRIBUTING.md).

Code is provided as is under [MIT](LICENSE), without a guarantee of suitability for every device or scene. [Sources and licenses](THIRD_PARTY_NOTICES.md) lists reference projects and external tools.
