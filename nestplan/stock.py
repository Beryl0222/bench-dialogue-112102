"""合成棒料库存与区间记账。

- free/occupied 都是从杆基准端计的半开区间 [start, end)。
- ``locked_refs`` 是本会话之外已约定的占用，任何试排都不能挪动。
- ``arrives_after`` 指向话轮 id，只有当前 seq 超过该话轮后杆才可用，
  用来表达“试排以后才到货”的库存（如 S5 要 M5 之后才到）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Interval:
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start

    def as_pair(self) -> list[float]:
        return [self.start, self.end]


@dataclass
class Bar:
    id: str
    grade: str
    direction: list[str]
    free: list[Interval]
    occupied: list[Interval]
    locked_refs: list[str] = field(default_factory=list)
    offcut: bool = False
    arrives_after: str | None = None

    @classmethod
    def from_row(cls, row: dict) -> "Bar":
        return cls(
            id=row["id"],
            grade=row["grade"],
            direction=list(row["direction"]),
            free=[Interval(a, b) for a, b in row["free"]],
            occupied=[Interval(a, b) for a, b in row["occupied"]],
            locked_refs=list(row.get("locked_refs", [])),
            offcut=bool(row.get("offcut", False)),
            arrives_after=row.get("arrives_after"),
        )

    def available_at(self, seq: int, seq_by_message: dict[str, int]) -> bool:
        if self.arrives_after is None:
            return True
        arrive_seq = seq_by_message.get(self.arrives_after)
        if arrive_seq is None:
            return False
        return seq > arrive_seq

    def grade_ok(self, grade: str) -> bool:
        return self.grade == grade

    def direction_ok(self, wanted: str) -> bool:
        return wanted in self.direction

    def longest_free(self) -> float:
        return max((iv.length for iv in self.free), default=0.0)

    def claim(self, interval: Interval, ref: str) -> None:
        """在指定自由区间上占位（半开区间），把剩余部分留回 free。"""
        remaining: list[Interval] = []
        hit = False
        for iv in self.free:
            if not hit and iv.start <= interval.start and interval.end <= iv.end:
                hit = True
                if interval.start > iv.start:
                    remaining.append(Interval(iv.start, interval.start))
                if interval.end < iv.end:
                    remaining.append(Interval(interval.end, iv.end))
            else:
                remaining.append(iv)
        if not hit:
            raise ValueError(
                f"杆 {self.id} 上的区间 {interval.as_pair()} 不在任何自由段内")
        self.free = remaining
        self.occupied = sorted(
            self.occupied + [interval], key=lambda x: x.start)
        self.locked_refs.append(ref)

    def release(self, interval: Interval, ref: str) -> None:
        """释放本会话先前占用的区间；locked_refs 里的外部约定不动。"""
        if ref not in self.locked_refs:
            raise ValueError(f"杆 {self.id} 没有本会话引用 {ref}")
        self.occupied = [
            iv for iv in self.occupied
            if not (iv.start == interval.start and iv.end == interval.end)
        ]
        self.locked_refs.remove(ref)
        merged = Interval(interval.start, interval.end)
        self.free.append(merged)
        # 合并相邻自由段
        changed = True
        free = sorted(self.free, key=lambda x: x.start)
        while changed:
            changed = False
            out: list[Interval] = []
            for iv in free:
                if out and out[-1].end == iv.start:
                    out[-1] = Interval(out[-1].start, iv.end)
                    changed = True
                else:
                    out.append(iv)
            free = out
        self.free = free


def load_bars(rows: list[dict]) -> dict[str, Bar]:
    return {row["id"]: Bar.from_row(row) for row in rows}
