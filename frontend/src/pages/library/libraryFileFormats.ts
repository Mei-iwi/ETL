export const LIBRARY_FILE_ACCEPT = '.pdf,.docx,.xlsx,.txt,.csv,.png,.jpg,.jpeg,.pptx,.mp3,.mp4'
export const LIBRARY_FILE_FORMATS = 'PDF · DOCX · XLSX · TXT · CSV · PNG · JPG · PPTX · MP3 · MP4'

export function libraryFileError(file: Pick<File, 'name' | 'size'>): string | null {
    if (!LIBRARY_FILE_ACCEPT.split(',').some(extension => file.name.toLowerCase().endsWith(extension)))
        return 'Định dạng không được hỗ trợ. Hãy chọn PDF, DOCX, XLSX, TXT, CSV, PNG, JPG, PPTX, MP3 hoặc MP4.'
    return file.size > 0 ? null : 'Tệp đang trống. Vui lòng chọn tệp khác.'
}

export function mediaPreviewType(mime: string): 'audio' | 'video' | null {
    const type = mime.split(';', 1)[0].trim().toLowerCase()
    return type === 'audio/mpeg' || type === 'audio/mp3' ? 'audio' : type === 'video/mp4' ? 'video' : null
}
