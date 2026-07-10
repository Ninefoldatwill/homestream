"""
硬件配置文件 - 硬件自适应核心

设计理念（九重定调）：
  "作为技术开源，未来大家可以根据每个人的硬件锚点开拓最优适配自己的AI生态园"

能力：
  1. 自动检测当前机器硬件（RAM/VRAM/GPU/CPU）
  2. 根据硬件参数推荐最优模型配置
  3. 支持手动覆盖配置（.env 或配置文件）
  4. 开源用户可自定义硬件档位

硬件档位参考：
  | 档位  | RAM    | VRAM   | 推荐模型           | 部署方式     |
  |-------|--------|--------|--------------------|--------------|
  | Nano  | 8GB    | 无GPU  | Qwen-1.5B Q4       | CPU only     |
  | Micro | 16GB   | 4GB    | Qwen2.5-7B Q4      | 部分GPU      |
  | Lite  | 16GB   | 6GB    | Qwen2.5-7B Q4_K_M  | 全GPU offload|
  | Std   | 32GB   | 8GB    | Qwen3.5-9B Q4      | 全GPU offload|
  | Pro   | 64GB   | 16GB   | GLM-4-9B Q4        | 全GPU offload|
  | Max   | 256GB+ | 48GB+  | GLM-5.2 2bit       | 多卡         |
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class HardwareTier(Enum):
    """硬件档位枚举"""

    NANO = "nano"  # 8GB RAM, 无GPU
    MICRO = "micro"  # 16GB RAM, 4GB VRAM
    LITE = "lite"  # 16GB RAM, 6GB VRAM (九重当前)
    STD = "std"  # 32GB RAM, 8GB VRAM
    PRO = "pro"  # 64GB RAM, 16GB VRAM
    MAX = "max"  # 256GB+ RAM, 48GB+ VRAM


@dataclass
class HardwareInfo:
    """硬件信息数据类"""

    total_ram_gb: float = 0.0
    available_ram_gb: float = 0.0
    gpu_name: str = "无GPU"
    gpu_vram_total_mb: int = 0
    gpu_vram_free_mb: int = 0
    cpu_cores: int = 0
    os_type: str = "unknown"

    @property
    def has_gpu(self) -> bool:
        return self.gpu_vram_total_mb > 0

    @property
    def gpu_vram_total_gb(self) -> float:
        return self.gpu_vram_total_mb / 1024.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_ram_gb": round(self.total_ram_gb, 1),
            "available_ram_gb": round(self.available_ram_gb, 1),
            "gpu_name": self.gpu_name,
            "gpu_vram_total_gb": round(self.gpu_vram_total_gb, 1),
            "gpu_vram_free_gb": round(self.gpu_vram_free_mb / 1024.0, 1),
            "cpu_cores": self.cpu_cores,
            "os_type": self.os_type,
        }


@dataclass
class ModelRecommendation:
    """模型推荐结果"""

    tier: HardwareTier
    model_name: str
    quantization: str
    estimated_ram_gb: float
    estimated_vram_gb: float
    can_full_gpu_offload: bool
    deployment_method: str
    notes: str = ""


def detect_hardware() -> HardwareInfo:
    """自动检测当前机器硬件信息"""
    info = HardwareInfo()

    # --- 内存检测 (Windows API) ---
    try:

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
        info.total_ram_gb = mem.ullTotalPhys / (1024**3)
        info.available_ram_gb = mem.ullAvailPhys / (1024**3)
        info.os_type = "windows"
    except Exception as e:
        logger.warning(f"内存检测失败: {e}")
        info.os_type = "unknown"

    # --- GPU检测 (nvidia-smi) ---
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(", ")
            if len(parts) >= 3:
                info.gpu_name = parts[0].strip()
                info.gpu_vram_total_mb = int(parts[1].strip())
                info.gpu_vram_free_mb = int(parts[2].strip())
    except FileNotFoundError:
        logger.info("nvidia-smi 未找到，可能无NVIDIA GPU")
    except Exception as e:
        logger.warning(f"GPU检测失败: {e}")

    # --- CPU核心数 ---
    try:
        info.cpu_cores = os.cpu_count() or 0
    except Exception:
        info.cpu_cores = 0

    return info


def recommend_tier(info: HardwareInfo) -> HardwareTier:
    """根据硬件信息推荐档位

    注意：16GB物理内存实际显示约15.7GB（系统保留），阈值取15
          8GB物理内存实际显示约7.5GB，阈值取7
    """
    ram = info.total_ram_gb
    vram = info.gpu_vram_total_gb

    if ram >= 240 and vram >= 48:
        return HardwareTier.MAX
    elif ram >= 60 and vram >= 16:
        return HardwareTier.PRO
    elif ram >= 30 and vram >= 8:
        return HardwareTier.STD
    elif ram >= 15 and vram >= 5:
        return HardwareTier.LITE
    elif ram >= 15 and vram >= 3:
        return HardwareTier.MICRO
    elif ram >= 7:
        return HardwareTier.NANO
    else:
        return HardwareTier.NANO


def get_model_recommendation(tier: HardwareTier) -> ModelRecommendation:
    """根据档位获取模型推荐"""
    recommendations = {
        HardwareTier.NANO: ModelRecommendation(
            tier=HardwareTier.NANO,
            model_name="Qwen2.5-1.5B",
            quantization="Q4_K_M",
            estimated_ram_gb=1.5,
            estimated_vram_gb=0,
            can_full_gpu_offload=False,
            deployment_method="llama.cpp CPU only",
            notes="入门级，CPU推理，速度较慢但可用",
        ),
        HardwareTier.MICRO: ModelRecommendation(
            tier=HardwareTier.MICRO,
            model_name="Qwen2.5-7B",
            quantization="Q4_K_M",
            estimated_ram_gb=5.0,
            estimated_vram_gb=4.0,
            can_full_gpu_offload=False,
            deployment_method="llama.cpp 部分GPU offload",
            notes="4GB VRAM只能放部分层到GPU，剩余在CPU",
        ),
        HardwareTier.LITE: ModelRecommendation(
            tier=HardwareTier.LITE,
            model_name="Qwen2.5-7B",
            quantization="Q4_K_M",
            estimated_ram_gb=5.0,
            estimated_vram_gb=4.5,
            can_full_gpu_offload=True,
            deployment_method="llama.cpp 全GPU offload (-ngl 99)",
            notes="6GB VRAM可全offload，推理速度好",
        ),
        HardwareTier.STD: ModelRecommendation(
            tier=HardwareTier.STD,
            model_name="Qwen3.5-9B",
            quantization="Q4_K_M",
            estimated_ram_gb=6.0,
            estimated_vram_gb=5.5,
            can_full_gpu_offload=True,
            deployment_method="llama.cpp 全GPU offload (-ngl 99)",
            notes="9B模型，128K上下文，性价比最优",
        ),
        HardwareTier.PRO: ModelRecommendation(
            tier=HardwareTier.PRO,
            model_name="GLM-4-9B",
            quantization="Q4_K_M",
            estimated_ram_gb=6.0,
            estimated_vram_gb=5.5,
            can_full_gpu_offload=True,
            deployment_method="llama.cpp 全GPU offload",
            notes="16GB VRAM可跑更大模型或更高精度",
        ),
        HardwareTier.MAX: ModelRecommendation(
            tier=HardwareTier.MAX,
            model_name="GLM-5.2",
            quantization="2bit dynamic",
            estimated_ram_gb=245.0,
            estimated_vram_gb=0,
            can_full_gpu_offload=False,
            deployment_method="llama.cpp 多卡/CPU+GPU混合",
            notes="需要256GB RAM，82%精度保留",
        ),
    }
    return recommendations.get(tier, recommendations[HardwareTier.NANO])


def print_hardware_report():
    """打印硬件报告（白纸黑字表格风格）"""
    info = detect_hardware()
    tier = recommend_tier(info)
    rec = get_model_recommendation(tier)

    print("=" * 60)
    print("硬件锚点报告")
    print("=" * 60)
    print(f"{'项目':<20} {'值':<40}")
    print("-" * 60)
    print(f"{'操作系统':<20} {info.os_type:<40}")
    print(f"{'CPU核心数':<20} {info.cpu_cores:<40}")
    print(f"{'总内存':<20} {info.total_ram_gb:.1f} GB{'':<30}")
    print(f"{'可用内存':<20} {info.available_ram_gb:.1f} GB{'':<30}")
    print(f"{'GPU型号':<20} {info.gpu_name:<40}")
    print(f"{'GPU显存':<20} {info.gpu_vram_total_gb:.1f} GB{'':<30}")
    print(f"{'GPU可用显存':<20} {info.gpu_vram_free_mb / 1024:.1f} GB{'':<30}")
    print("-" * 60)
    print(f"{'推荐档位':<20} {tier.value.upper():<40}")
    print(f"{'推荐模型':<20} {rec.model_name:<40}")
    print(f"{'量化方式':<20} {rec.quantization:<40}")
    print(f"{'部署方式':<20} {rec.deployment_method:<40}")
    print(f"{'全GPU offload':<20} {'是' if rec.can_full_gpu_offload else '否':<40}")
    print(f"{'备注':<20} {rec.notes:<40}")
    print("=" * 60)

    return info, tier, rec


if __name__ == "__main__":
    print_hardware_report()
