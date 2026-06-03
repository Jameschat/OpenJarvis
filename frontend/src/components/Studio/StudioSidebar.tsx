import { ChevronDown, ChevronRight, MoreHorizontal, Plus, Search } from 'lucide-react';
import { useState } from 'react';
import type { StudioChat, StudioProject } from './types';

interface StudioSidebarProps {
  projects: StudioProject[];
  chats: StudioChat[];
  plugins: Array<Record<string, unknown>>;
  automations: Array<Record<string, unknown>>;
  selectedProjectId: string;
  selectedChatId: string;
  searchQuery: string;
  searchResults: Array<Record<string, unknown>>;
  onSearchQueryChange: (query: string) => void;
  onCreateChat: () => void;
  onArchiveChat: (chatId: string) => void;
  onDeleteChat: (chatId: string) => void;
  onSelectProject: (projectId: string) => void;
  onSelectChat: (chatId: string) => void;
}

export function StudioSidebar({
  projects,
  chats,
  plugins,
  automations,
  selectedProjectId,
  selectedChatId,
  searchQuery,
  searchResults,
  onSearchQueryChange,
  onCreateChat,
  onArchiveChat,
  onDeleteChat,
  onSelectProject,
  onSelectChat,
}: StudioSidebarProps) {
  const [openMenuChatId, setOpenMenuChatId] = useState('');
  const [pluginsOpen, setPluginsOpen] = useState(false);
  const [automationsOpen, setAutomationsOpen] = useState(false);

  return (
    <aside className="studio-sidebar">
      <button className="studio-sidebar-primary" type="button" onClick={onCreateChat}>
        <Plus size={16} />
        New chat
      </button>

      <label className="studio-search-box">
        <Search size={15} />
        <input
          value={searchQuery}
          onChange={(event) => onSearchQueryChange(event.target.value)}
          placeholder="Search Studio"
        />
      </label>
      {searchQuery && (
        <div className="studio-sidebar-results">
          {searchResults.length === 0 ? (
            <div className="studio-muted">No results</div>
          ) : (
            searchResults.slice(0, 6).map((result, index) => (
              <button
                className="studio-search-result"
                key={`${String(result.type || 'result')}-${index}`}
                type="button"
                onClick={() => {
                  const projectId = String(result.project_id || '');
                  const chatId = String(result.chat_id || '');
                  if (projectId) onSelectProject(projectId);
                  if (chatId) onSelectChat(chatId);
                }}
              >
                <strong>{String(result.type || 'result')}</strong>
                <span>{String(result.title || result.snippet || result.run_id || 'Studio result')}</span>
              </button>
            ))
          )}
        </div>
      )}

      <section className="studio-sidebar-section">
        <div className="studio-sidebar-heading">Projects</div>
        {projects.length === 0 ? (
          <div className="studio-muted">No projects loaded</div>
        ) : (
          projects.map((project) => (
            <button
              className={`studio-row-button ${project.id === selectedProjectId ? 'active' : ''}`}
              key={project.id}
              onClick={() => onSelectProject(project.id)}
              type="button"
            >
              <span>{project.title || project.vault_project || project.id}</span>
            </button>
          ))
        )}
      </section>

      <section className="studio-sidebar-section">
        <div className="studio-sidebar-heading">Chats</div>
        {chats.length === 0 ? (
          <div className="studio-muted">No chats</div>
        ) : (
          chats.map((chat) => (
            <div className="studio-chat-row" key={chat.id}>
              <button
                className={`studio-row-button ${chat.id === selectedChatId ? 'active' : ''}`}
                onClick={() => onSelectChat(chat.id)}
                type="button"
              >
                <span>{chat.title || 'New chat'}</span>
              </button>
              <button
                className="studio-chat-menu-button"
                type="button"
                aria-label={`Open chat menu for ${chat.title || 'New chat'}`}
                onClick={() => setOpenMenuChatId((current) => (current === chat.id ? '' : chat.id))}
              >
                <MoreHorizontal size={15} />
              </button>
              {openMenuChatId === chat.id && (
                <div className="studio-chat-menu">
                  <button type="button" onClick={() => onArchiveChat(chat.id)}>Archive</button>
                  <button type="button" onClick={() => onDeleteChat(chat.id)}>Delete</button>
                </div>
              )}
            </div>
          ))
        )}
      </section>

      <div className="studio-sidebar-footer">
        <button className="studio-sidebar-link" type="button" onClick={() => setPluginsOpen((open) => !open)}>
          {pluginsOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          Plugins
        </button>
        {pluginsOpen && (
          <div className="studio-sidebar-drawer">
            {plugins.length === 0 ? (
              <div className="studio-muted">No plugins reported</div>
            ) : (
              plugins.slice(0, 8).map((plugin) => (
                <div className="studio-drawer-row" key={String(plugin.id || plugin.name)}>
                  <span>{String(plugin.name || plugin.id || 'Plugin')}</span>
                  <strong>{String(plugin.status || 'online')}</strong>
                </div>
              ))
            )}
          </div>
        )}
        <button className="studio-sidebar-link" type="button" onClick={() => setAutomationsOpen((open) => !open)}>
          {automationsOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          Automations
          <strong>{automations.length}</strong>
        </button>
        {automationsOpen && (
          <div className="studio-sidebar-drawer">
            {automations.length === 0 ? (
              <div className="studio-muted">No automations scheduled</div>
            ) : (
              automations.slice(0, 8).map((automation, index) => (
                <div className="studio-drawer-row" key={String(automation.id || automation.name || index)}>
                  <span>{String(automation.name || automation.title || automation.agent || 'Automation')}</span>
                  <strong>{String(automation.status || automation.next_run || '')}</strong>
                </div>
              ))
            )}
          </div>
        )}
        <button className="studio-sidebar-link" type="button" disabled title="Studio settings are wired in the next Studio phase">Settings</button>
      </div>
    </aside>
  );
}
