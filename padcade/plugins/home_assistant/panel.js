// The Home Assistant page's own part of the management page: reading again what Home Assistant has.
export default kit => ({
  view: () => [kit.head('Home Assistant'), kit.el('p', {role: 'status', textContent: kit.plugin().state.status === 'Connected.'
    ? 'Connected to Home Assistant. Credentials saved.' : kit.plugin().state.status})],
  main: standard => [...standard, kit.el('button', {className: 'quiet', style: 'justify-self:start', textContent: `Reload devices (${kit.plugin().items.length})`,
    onclick: async () => { await kit.ask('look', {}); setTimeout(kit.refresh, 1500); }})],
});
