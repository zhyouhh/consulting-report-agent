import React, { useState, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import axios from 'axios'

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
      setCurrentFile('plan/project-info.md')
      loadFile('plan/project-info.md')
    } else {
      setFiles([])
      setContent('')
    }
  }, [project, loadFiles, loadFile])

  return (
    <div className="w-96 bg-white border-l border-gray-200 flex flex-col">
      <div className="p-4 border-b border-gray-200">
        <h3 className="font-semibold text-gray-800">文件预览</h3>
      </div>

      <div className="border-b border-gray-200 max-h-64 overflow-y-auto">
        {files.map(file => (
          <div
            key={file.path}
            onClick={() => loadFile(file.path)}
            className={`px-4 py-2 cursor-pointer text-sm ${
              currentFile === file.path ? 'bg-blue-50 text-blue-600' : 'hover:bg-gray-50 text-gray-700'
            }`}
          >
            {file.name}
          </div>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <div className="prose prose-sm max-w-none">
          <ReactMarkdown>{content}</ReactMarkdown>
        </div>
      </div>
    </div>
  )
}
