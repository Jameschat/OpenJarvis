import { MoreHorizontal, Plus, Search } from 'lucide-react';
import type { StudioChat, StudioProject } from './types';

interface StudioSidebarProps {
  projects: StudioProject[];
  chats: StudioChat[];
  selectedProjectId: string;
  selectedChatId: string;
  onSelectProject: (projectId: string) => void;
  onSelectChat: (chatId: string) => void;
}

export function StudioSidebar({
  projects,
  chats,
  selectedProjectId,
  selectedChatId,
  onSelectProject,
  onSelectChat,
}: StudioSidebarProps) {
  return (
    <aside className="studio-sidebar">
      <button className="studio-sidebar-primary" type="button" disabled title="New chat action is wired in the next Studio phase">
        <Plus size={16} />
        New chat
      </button>

      <button className="studio-sidebar-link" type="button" disabled title="Search is wired in the next Studio phase">
        <Search size={15} />
        Search
      </button>

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
            <button
              className={`studio-row-button ${chat.id === selectedChatId ? 'active' : ''}`}
              key={chat.id}
              onClick={() => onSelectChat(chat.id)}
              type="button"
            >
              <span>{chat.title || 'New chat'}</span>
              <MoreHorizontal size={15} />
            </button>
          ))
        )}
      </section>

      <div className="studio-sidebar-footer">
        <button className="studio-sidebar-link" type="button" disabled title="Plugin browser is wired in the next Studio phase">Plugins</button>
        <button className="studio-sidebar-link" type="button" disabled title="Automation browser is wired in the next Studio phase">Automations</button>
        <button className="studio-sidebar-link" type="button" disabled title="Studio settings are wired in the next Studio phase">Settings</button>
      </div>
    </aside>
  );
}
