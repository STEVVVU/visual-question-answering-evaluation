"""Offline, bounded CPU model process. Input contains no gold annotations."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def main():
    started = time.perf_counter()
    import torch
    from PIL import Image
    from transformers import AutoProcessor, AutoModelForImageTextToText

    source, model_path, output = map(Path, sys.argv[1:4])
    request = json.loads(source.read_text(encoding="utf-8"))
    torch.set_num_threads(4)
    processor = AutoProcessor.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=False)
    model = AutoModelForImageTextToText.from_pretrained(
        str(model_path), local_files_only=True, trust_remote_code=False,
        use_safetensors=True, dtype=torch.float32, attn_implementation="eager").to("cpu").eval()
    model_load_seconds = time.perf_counter() - started
    # An actual forward hook records that generation traverses the vision encoder.
    vision_calls = [0]
    vision = model.model.vision_model
    hook = vision.register_forward_hook(lambda *_: vision_calls.__setitem__(0, vision_calls[0] + 1))
    with output.open("w", encoding="utf-8") as out:
        for item in request["items"]:
            item_started = time.perf_counter()
            image = Image.open(io.BytesIO(base64.b64decode(item["image"]))).convert("RGB")
            question = item["question"]
            if item["context"]:
                question = item["context"] + "\n" + question
            if item["answer_mode"] == "mcq":
                question += "\n" + "\n".join(f"{chr(65+i)}. {x}" for i, x in enumerate(item["choices"]))
                question += "\nAnswer with the letter of the correct choice."
            else:
                question += "\nGive a short answer."
            messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": question}]}]
            prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
            inputs = processor(text=prompt, images=[image], return_tensors="pt")
            before = vision_calls[0]
            generation_started = time.perf_counter()
            with torch.inference_mode():
                generated = model.generate(**inputs, **request["generation"])
            generation_seconds = time.perf_counter() - generation_started
            prediction = processor.batch_decode(generated[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0]
            record = {"id": item["id"], "prediction": prediction, "vision_forwards": vision_calls[0] - before,
                      "pixel_shape": list(inputs["pixel_values"].shape), "device": "cpu",
                      "prompt_sha256": hashlib.sha256(question.encode()).hexdigest()}
            record.update(generation_seconds=generation_seconds, item_seconds=time.perf_counter() - item_started,
                          model_load_seconds=model_load_seconds, cpu_seconds=time.process_time())
            record["generated_tokens"] = generated.shape[1] - inputs["input_ids"].shape[1]
            if sys.platform == "linux":
                import resource
                record["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if record["vision_forwards"] < 1:
                raise RuntimeError("Generation did not execute the vision encoder")
            out.write(json.dumps(record) + "\n")
            out.flush()
    hook.remove()


if __name__ == "__main__":
    if os.environ.get("VQA_PROFILE_DIR"):
        import cProfile
        root = Path(os.environ["VQA_PROFILE_DIR"]).resolve()
        root.mkdir(parents=True, exist_ok=True)
        cProfile.run("main()", str(root / f"model-{os.getpid()}.pstats"))
    else:
        main()
