/*
 * EverRec 界面设计稿的离线预览运行时。
 * 把设计稿里的 {{变量}}、循环（data-for）、条件（data-if）和点击事件渲染成普通网页，
 * 不依赖任何外部服务，双击 HTML 文件即可在浏览器中打开。
 */
(function () {
  'use strict';

  class DCLogic {
    constructor(props) {
      this.props = props || {};
      this.state = {};
    }
    setState(patch) {
      const next = typeof patch === 'function' ? patch(this.state, this.props) : patch;
      Object.assign(this.state, next);
      render();
    }
    forceUpdate() {
      render();
    }
  }
  window.DCLogic = DCLogic;

  const HOLE = /\{\{\s*([^}]+?)\s*\}\}/g;

  function resolve(path, scope) {
    path = String(path).trim();
    if (path === 'true') return true;
    if (path === 'false') return false;
    if (/^-?\d+(\.\d+)?$/.test(path)) return Number(path);
    let value = scope;
    for (const key of path.split('.')) {
      if (value == null) return undefined;
      value = value[key];
    }
    return value;
  }

  function single(text) {
    const m = String(text).match(/^\s*\{\{\s*([^}]+?)\s*\}\}\s*$/);
    return m ? m[1] : null;
  }

  function interpolate(text, scope) {
    return text.replace(HOLE, (_, path) => {
      const v = resolve(path, scope);
      return v == null ? '' : String(v);
    });
  }

  function build(node, scope, out) {
    if (node.nodeType === Node.TEXT_NODE) {
      out.push(document.createTextNode(interpolate(node.nodeValue, scope)));
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;

    if (node.tagName === 'TEMPLATE' && node.hasAttribute('data-for')) {
      const list = resolve(single(node.getAttribute('data-for')), scope) || [];
      const as = node.getAttribute('data-as') || 'item';
      list.forEach((item, index) => {
        const child = Object.create(scope);
        child[as] = item;
        child.$index = index;
        node.content.childNodes.forEach((c) => build(c, child, out));
      });
      return;
    }
    if (node.tagName === 'TEMPLATE' && node.hasAttribute('data-if')) {
      if (resolve(single(node.getAttribute('data-if')), scope)) {
        node.content.childNodes.forEach((c) => build(c, scope, out));
      }
      return;
    }

    const el = node.cloneNode(false);
    for (const attr of Array.from(node.attributes)) {
      const { name, value } = attr;
      if (name.startsWith('hint-')) {
        el.removeAttribute(name);
        continue;
      }
      if (name.startsWith('on')) {
        el.removeAttribute(name);
        const path = single(value);
        const fn = path && resolve(path, scope);
        if (typeof fn === 'function') el.addEventListener(name.slice(2), fn);
        continue;
      }
      if (value.indexOf('{{') >= 0) {
        const path = single(value);
        if (path) {
          const v = resolve(path, scope);
          if (v === false || v == null) el.removeAttribute(name);
          else el.setAttribute(name, v === true ? '' : String(v));
        } else {
          el.setAttribute(name, interpolate(value, scope));
        }
      }
    }
    const kids = [];
    node.childNodes.forEach((c) => build(c, scope, kids));
    kids.forEach((k) => el.appendChild(k));
    out.push(el);
  }

  let component = null;

  function render() {
    if (!component) return;
    const template = document.getElementById('dc-template');
    const root = document.getElementById('dc-root');
    const scope = Object.assign(Object.create(null), component.renderVals());
    const out = [];
    template.content.childNodes.forEach((c) => build(c, scope, out));
    root.replaceChildren(...out);
  }

  window.__dcBoot = function (Component, defaults) {
    const props = Object.assign({}, defaults);
    new URLSearchParams(location.search).forEach((v, k) => {
      props[k] = v;
    });
    component = new Component(props);
    component.props = props;
    render();
    if (typeof component.componentDidMount === 'function') component.componentDidMount();
  };
})();
