import { type ComponentProps, useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  ActionLink,
  Breadcrumbs,
  Button,
  CheckboxField,
  ContentCard,
  Dialog,
  DialogActions,
  DialogHeading,
  DisclosureMenu,
  DocumentFrame,
  IconButton,
  MetadataDisclosure,
  Popup,
  SelectField,
  SidePanel,
  StatusMessage,
  Tabs,
  TextField,
  Toolbar,
} from "./ui";
describe("shared controls", () => {
  it("defaults to a non-submitting button and forwards disabled and accessible names", () => {
    const click = vi.fn();
    render(
      <form>
        <Button disabled aria-label="保存" onClick={click}>
          保存
        </Button>
      </form>,
    );
    const b = screen.getByRole("button", { name: "保存" });
    expect(b).toHaveAttribute("type", "button");
    fireEvent.click(b);
    expect(click).not.toHaveBeenCalled();
  });
  it("supports keyboard navigation and renders untrusted labels as text", () => {
    const change = vi.fn();
    render(
      <Tabs
        label="表示"
        items={[
          { id: "a", label: "<script>bad</script>" },
          { id: "b", label: "本文" },
        ]}
        value="a"
        onChange={change}
      />,
    );
    fireEvent.keyDown(
      screen.getByRole("tab", { name: "<script>bad</script>" }),
      { key: "ArrowRight" },
    );
    expect(change).toHaveBeenCalledWith("b");
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByRole("tab", { name: "本文" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("tab", { name: "本文" }), { key: "Home" });
    fireEvent.keyDown(screen.getByRole("tab", { name: "<script>bad</script>" }), {
      key: "End",
    });
    fireEvent.keyDown(screen.getByRole("tab", { name: "本文" }), { key: "ArrowLeft" });
    fireEvent.keyDown(screen.getByRole("tab", { name: "本文" }), { key: "Enter" });
    expect(change).toHaveBeenCalledWith("a");
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function close() {
    this.open = false;
  };
});

function DialogHarness({ busy = false }: { busy?: boolean }) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button onClick={() => setOpen(false)}>hide</button>
      <Dialog open={open} onClose={() => setOpen(false)} busy={busy}>
        <DialogHeading id="title" title="設定" onClose={() => setOpen(false)} disabled={busy} />
        <DialogActions>
          <button>保存</button>
        </DialogActions>
      </Dialog>
    </>
  );
}

describe("dialogs, menus, and popups", () => {
  it("renders fields, cards, and both toolbar elements", () => {
    render(
      <>
        <IconButton label="追加" icon="plus" />
        <IconButton label="文字">あ</IconButton>
        <ActionLink href="/files/a">開く</ActionLink>
        <TextField aria-label="名前" defaultValue="資料" />
        <SelectField aria-label="形式" defaultValue="pdf">
          <option value="pdf">PDF</option>
        </SelectField>
        <CheckboxField aria-label="選択" indeterminate />
        <Toolbar as="header">見出し</Toolbar>
        <Toolbar>本文</Toolbar>
        <StatusMessage>完了</StatusMessage>
        <ContentCard title="カード">中身</ContentCard>
        <MetadataDisclosure title="詳細">値</MetadataDisclosure>
        <DocumentFrame title="frame" />
        <SidePanel
          heading="h"
          title="訳文"
          closeId="close-tr"
          onClose={() => {}}
          headingClass="head"
        >
          訳
        </SidePanel>
        <SidePanel
          heading="h2"
          title="解説"
          closeId="close-ex"
          onClose={() => {}}
          headingClass="head"
        >
          解説本文
        </SidePanel>
      </>,
    );
    expect(screen.getByRole("button", { name: "追加" }).querySelector("use")).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "選択" })).toBeInTheDocument();
    expect(screen.getByRole("banner")).toHaveTextContent("見出し");
    expect(screen.getByRole("button", { name: "訳文を閉じる" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "解説を閉じる" })).toBeInTheDocument();
  });

  it("closes a dialog from cancel, the heading, and unmount", () => {
    const view = render(<DialogHarness />);
    const dialog = view.container.querySelector("dialog")!;
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(dialog.open).toBe(false);
    view.unmount();
    const busyView = render(<DialogHarness busy />);
    const busy = busyView.container.querySelector("dialog")!;
    fireEvent(busy, new Event("cancel", { cancelable: true }));
    expect(busy.open).toBe(true);
    fireEvent(busy, new Event("close"));
    expect(busy.open).toBe(false);
    busyView.unmount();
  });

  it("closes menus on escape, outside click, and item activation", () => {
    const view = render(
      <>
        <DisclosureMenu label="操作" icon="more">
          <button>項目</button>
        </DisclosureMenu>
        <DisclosureMenu label="別の操作" icon="more" className="item-menu">
          <a href="#x">リンク</a>
        </DisclosureMenu>
      </>,
    );
    const menus = view.container.querySelectorAll("details");
    menus[0].open = true;
    fireEvent(menus[0], new Event("toggle"));
    expect(menus[1].open).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "項目" }));
    menus[0].open = true;
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(menus[0].open).toBe(false);
    menus[0].open = true;
    document.body.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(menus[0].open).toBe(false);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    expect(menus[0].open).toBe(false);
  });

  it("places a menu popup and walks its keys", () => {
    const anchor = document.createElement("button");
    anchor.textContent = "anchor";
    document.body.append(anchor);
    const close = vi.fn();
    const rect = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({
        left: 10,
        bottom: 20,
        top: 0,
        right: 0,
        width: 10,
        height: 20,
        x: 10,
        y: 0,
        toJSON() {
          return {};
        },
      });
    const view = render(
      <Popup position={{ anchor }} onClose={close} menu>
        <button>一つ</button>
        <button>二つ</button>
      </Popup>,
    );
    const menu = screen.getByRole("menu", { name: "表の操作メニュー" });
    const [first, second] = screen.getAllByRole("button", { name: /一つ|二つ/ });
    second.focus();
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    fireEvent.keyDown(menu, { key: "End" });
    fireEvent.keyDown(menu, { key: "Home" });
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    second.focus();
    fireEvent.keyDown(menu, { key: "Tab" });
    first.focus();
    fireEvent.keyDown(menu, { key: "Tab", shiftKey: true });
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(close).toHaveBeenCalled();
    fireEvent.pointerDown(document.body);
    fireEvent(window, new Event("resize"));
    rect.mockReturnValue({
      left: 0,
      bottom: 5000,
      top: 0,
      right: 0,
      width: 10,
      height: 400,
      x: 0,
      y: 0,
      toJSON() {
        return {};
      },
    });
    view.rerender(
      <Popup position={{ anchor, x: 9000, y: 9000 }} onClose={close}>
        <input aria-label="値" />
      </Popup>,
    );
    expect(screen.getByRole("dialog", { name: "表の設定" })).toBeInTheDocument();
    const dialog = document.createElement("dialog");
    dialog.open = true;
    dialog.append(anchor);
    document.body.append(dialog);
    view.rerender(
      <Popup position={{ anchor }} onClose={close}>
        <button>中</button>
      </Popup>,
    );
    expect(dialog.querySelector(".table-popup")).toBeTruthy();
    view.rerender(
      <Popup position={null} onClose={close}>
        <button>隠す</button>
      </Popup>,
    );
    expect(screen.queryByRole("dialog", { name: "表の設定" })).toBeNull();
    rect.mockRestore();
    dialog.remove();
    anchor.remove();
  });

  it("moves breadcrumb selection and drop targets", () => {
    const select = vi.fn();
    render(
      <Breadcrumbs
        aria-label="階層"
        items={[
          { id: null, name: "資料一覧" },
          { id: "f", name: "フォルダー" },
        ]}
        onSelect={select}
        dropProps={(id): ComponentProps<"button"> & { "data-drop": string } => ({
          "data-drop": id || "root",
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "フォルダー" }));
    expect(select).toHaveBeenCalledWith("f");
    expect(screen.getByRole("button", { name: "資料一覧" })).toHaveAttribute(
      "data-drop",
      "root",
    );
  });
});
