import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { UserPicker } from "./UserPicker";

const users = [
  { user_id: "U-201", name: "Michael Turner", role: "Production supervisor" },
  { user_id: "U-202", name: "Jessica Reed", role: "Field inspector" },
  { user_id: "U-204", name: "Tyler Hughes", role: "Field operator" },
];

describe("UserPicker", () => {
  it("exposes unauthenticated identity and selects with the keyboard", () => {
    const onChange = vi.fn();
    render(<UserPicker users={users} value="U-201" onChange={onChange} />);

    const trigger = screen.getByRole("button", { name: /Acting as \(not authenticated\).*Michael Turner/ });
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    const jessica = screen.getByRole("option", { name: /Jessica Reed.*Field inspector/ });
    expect(jessica).toHaveFocus();
    fireEvent.keyDown(jessica, { key: "Enter" });

    expect(onChange).toHaveBeenCalledWith("U-202");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("closes without changing the user on Escape", () => {
    const onChange = vi.fn();
    render(<UserPicker users={users} value="U-201" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /Acting as \(not authenticated\)/ }));
    const selected = screen.getByRole("option", { name: /Michael Turner/ });
    fireEvent.keyDown(selected, { key: "Escape" });
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});
