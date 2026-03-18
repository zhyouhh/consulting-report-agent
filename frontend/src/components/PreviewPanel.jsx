import React, { useState, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeHighlight from 'rehype-highlight'
import rehypeRaw from 'rehype-raw'
import axios from 'axios'
import 'highlight.js/styles/github-dark.css'
import 'katex/dist/katex.min.css'

export default function PreviewPanel({ project }) {
  const [files, setFiles] = useState([])
  const [currentFile, setCurrentFile] = useState('plan/project-info.md')
  const [content, setContent] = useState('')

  const loadFiles = useCallback(async () => {
    if (!project) return
    try {
      const res = await axios.get(`/api/projects/${encodeURIComponent(project)}/files`)
      const fileList = res.data.files.map(path => ({
        name: path.split('/').pop().replace('.md', ''),
        path: path
      }))
      setFiles(fileList)
    } catch (error) {
      console.error('加载文件列表失败', error)
    }
  }, [project])

  const loadFile = useCallback(async (path) => {
    if (!project) return
    try {
      const res = await axios.get(`/api/projects/${encodeURIComponent(project)}/files/${path}`)
      setContent(res.data.content)
      setCurrentFile(path)
    } catch (error) {
      setContent('文件不存在或无法读取')
    }
  }, [project])

  useEffect(() => {
    if (project) {
      loadFiles()
      const defaultFile = 'plan/project-info.md'
      setCurrentFile(defaultFile)
      loadFile(defaultFile)
    } else {
      setFiles([])
      setContent('')
    }
  }, [project, loadFiles, loadFile])

  return (
    <div className="w-96 bg-[#1a1a2e] border-l border-[#2a2a4a] flex flex-col">
      <div className="p-4 border-b border-[#2a2a4a]">
        <h3 className="font-semibold text-[#e2e2f0]">文件预览</h3>
      </div>

      <div className="border-b border-[#2a2a4a] max-h-64 overflow-y-auto">
        {files.map(file => (
          <div
            key={file.path}
            onClick={() => loadFile(file.path)}
            className={`px-4 py-2 cursor-pointer text-sm ${
              currentFile === file.path ? 'bg-[#1e1e4a] text-blue-400' : 'hover:bg-[#222244] text-[#c8c8e0]'
            }`}
          >
            {file.name}
          </div>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-6 bg-[#0d0d1a]">
        <div className="markdown-body max-w-none">
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkMath]}
            rehypePlugins={[rehypeKatex, rehypeHighlight, rehypeRaw]}
            components={{
              code: ({node, inline, className, children, ...props}) => {
                return inline ? (
                  <code className="px-1.5 py-0.5 bg-[#1a1a2e] text-[#64ffda] rounded text-sm font-mono" {...props}>
                    {children}
                  </code>
                ) : (
                  <code className={className} {...props}>
                    {children}
                  </code>
                )
              },
              table: ({children}) => (
                <div className="overflow-x-auto my-4">
                  <table className="min-w-full border-collapse border border-[#2a2a4a]">
                    {children}
                  </table>
                </div>
              ),
              th: ({children}) => (
                <th className="border border-[#2a2a4a] bg-[#1a1a2e] px-4 py-2 text-left text-[#64ffda] font-semibold">
                  {children}
                </th>
              ),
              td: ({children}) => (
                <td className="border border-[#2a2a4a] px-4 py-2 text-[#e2e2f0]">
                  {children}
                </td>
              ),
              img: ({src, alt}) => (
                <img src={src} alt={alt} className="max-w-full h-auto rounded-lg shadow-lg my-4" />
              ),
              a: ({href, children}) => (
                <a href={href} className="text-[#64ffda] hover:text-[#52e0c2] underline" target="_blank" rel="noopener noreferrer">
                  {children}
                </a>
              ),
              blockquote: ({children}) => (
                <blockquote className="border-l-4 border-[#64ffda] pl-4 py-2 my-4 bg-[#1a1a2e] text-[#c8c8e0] italic">
                  {children}
                </blockquote>
              ),
              h1: ({children}) => <h1 className="text-3xl font-bold text-[#e2e2f0] mt-6 mb-4 pb-2 border-b border-[#2a2a4a]">{children}</h1>,
              h2: ({children}) => <h2 className="text-2xl font-bold text-[#e2e2f0] mt-5 mb-3">{children}</h2>,
              h3: ({children}) => <h3 className="text-xl font-semibold text-[#e2e2f0] mt-4 mb-2">{children}</h3>,
              p: ({children}) => <p className="text-[#c8c8e0] leading-7 mb-4">{children}</p>,
              ul: ({children}) => <ul className="list-disc list-inside text-[#c8c8e0] mb-4 space-y-2">{children}</ul>,
              ol: ({children}) => <ol className="list-decimal list-inside text-[#c8c8e0] mb-4 space-y-2">{children}</ol>,
            }}
          >
            {content}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  )
}
